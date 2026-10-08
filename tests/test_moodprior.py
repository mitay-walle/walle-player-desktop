import numpy as np

from musicplayer.core import moodprior
from musicplayer.core.scanner import scan


def test_genre_and_artist_priors_exclude_the_track_itself(db, library, tmp_path):
    scan(db, library, tmp_path / "c")
    ids = [t["id"] for t in db.search(limit=10)]
    for i in ids:
        db.set_genres([i], ["Metal"], "set")
    db.set_moods(ids[:2], ["Злое"], "set")
    nb = moodprior.Neighbours(db, ["Злое", "Спокойное"])
    g = nb.genre_prior(ids[2])                          # an unlabelled Metal track leans towards "Злое"
    assert g is not None and g[0] > g[1]
    P = np.array([[0.1, 0.9]])
    out = moodprior.blend(P, [ids[2]], nb, 0.5, 0.0)
    assert out[0, 0] > 0.1 and out[0, 1] < 0.9


def test_metal_and_punk_hint_heavy_even_without_labels(db, library, tmp_path):
    scan(db, library, tmp_path / "c")
    t = db.search(limit=1)[0]["id"]
    db.set_genres([t], ["Metal"], "set")
    db.set_moods([db.search(limit=2)[1]["id"]], ["Спокойное"], "set")
    nb = moodprior.Neighbours(db, ["Тяжёлое", "Спокойное"])
    g = nb.genre_prior(t)
    assert g is not None and g[0] > 0.3                # Metal leans towards "Тяжёлое" by the hint alone


def test_calm_instrumental_pulls_night_and_background(db, library, tmp_path):
    scan(db, library, tmp_path / "c")
    a, b = [t["id"] for t in db.search(limit=2)]
    db.set_vocal([a], "inst")
    db.set_vocal([b], "voc")
    moods = ["Спокойное", "Ночное", "Фоновое"]
    P = np.array([[0.8, 0.1, 0.1], [0.8, 0.1, 0.1]])
    out = moodprior.links(db, moods, [a, b], P)
    assert out[0, 1] >= 0.6 and out[0, 2] >= 0.6          # calm instrumental -> night and background
    assert out[1, 1] == 0.1                               # a calm song with vocals is left alone


def test_labelled_since_training(db, library, tmp_path):
    from musicplayer.core import mood
    scan(db, library, tmp_path / "c")
    ids = [t["id"] for t in db.search(limit=3)]
    db.set_moods(ids[:2], ["Злое"], "set")

    class M:
        info = {"train_ids": [ids[0]]}
    assert mood.labelled_since_training(db, M()) == 1
    assert mood.labelled_since_training(db, None) == 2
