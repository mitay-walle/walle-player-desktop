import numpy as np

from musicplayer.core import mood
from musicplayer.core.descriptors import DESCRIPTOR_NAMES


def fake_desc(kind, rng):
    d = {n: None for n in DESCRIPTOR_NAMES}
    base = {"sad": (60, -22, 800, "A minor"), "happy": (140, -9, 3500, "C major")}[kind]
    d.update(bpm=base[0] + rng.normal(0, 4), loudness=base[1] + rng.normal(0, 1),
             spectral_centroid=base[2] + rng.normal(0, 150), tonality=base[3])
    return d


def seed(db, n=30):
    rng = np.random.RandomState(1)
    ids = []
    for i in range(n):
        kind = "sad" if i % 2 else "happy"
        tid = db.upsert_track({"path": f"{kind}{i}.mp3", "title": f"t{i}", "artist": "a"}, [])
        db.set_descriptors(tid, fake_desc(kind, rng))
        ids.append((tid, kind))
    return ids


def test_train_predict_roundtrip(db, tmp_path):
    ids = seed(db)
    for tid, kind in ids[:24]:
        db.set_moods([tid], ["Грустное" if kind == "sad" else "Весёлое"])
    epochs = []
    res = mood.train(db, tmp_path, epochs=60, on_epoch=epochs.append, models=("mlp",))
    assert len(epochs) == 60 and epochs[0]["epoch"] == 1
    assert set(res["moods"]) == {"Грустное", "Весёлое"}
    assert res["val"]["f1"] > 0.8
    out = mood.predict(db, tmp_path)           # only unlabelled tracks
    assert out["predicted"] == 6
    for tid, kind in ids[24:]:
        t = db.get_track(tid)
        assert t["moods"] == []                # predictions are suggestions, not labels
        assert t["mood_suggestions"][0]["mood"] == ("Грустное" if kind == "sad" else "Весёлое")
        assert t["mood_suggestions"][0]["source"] == "model"


def test_too_few_labels_is_a_clear_error(db, tmp_path):
    import pytest
    seed(db, 6)
    with pytest.raises(ValueError, match="Мало размеченных"):
        mood.train(db, tmp_path)


def test_feature_vector_mode():
    d = fake_desc("sad", np.random.RandomState(0))
    assert mood.feature_vector(d)[-1] == 1.0


def test_rejected_suggestions_become_negatives_and_are_not_reoffered(db, tmp_path):
    ids = seed(db)
    for tid, kind in ids[:24]:
        db.set_moods([tid], ["Грустное" if kind == "sad" else "Весёлое"])
    tid = ids[24][0]
    db.suggest_moods([(tid, "Грустное", 0.9)], "model")
    db.resolve_suggestions([tid], accept=False)
    assert db.mood_negatives() == {tid: {"Грустное"}}
    assert db.suggest_moods([(tid, "Грустное", 0.9)], "model") == 0          # never offered again
    assert tid in mood.training_matrix(db)[0]                               # used as a negative example
    db.set_moods([tid], ["Грустное"])                                       # an explicit label overrides the reject
    assert db.mood_negatives() == {}


def test_uncertain_tracks_and_next_step(db, tmp_path, monkeypatch):
    from musicplayer.core import clap
    monkeypatch.setattr(clap, "available", lambda: False)      # descriptors-only workflow
    ids = seed(db, 40)
    assert mood.next_step(db, tmp_path)["step"] == "label"
    for tid, kind in ids[:24]:
        db.set_moods([tid], ["Грустное" if kind == "sad" else "Весёлое"])
    assert mood.next_step(db, tmp_path)["step"] == "train"
    mood.train(db, tmp_path, epochs=40)
    assert mood.next_step(db, tmp_path)["step"] in ("predict", "review", "analyze_all", "done")
    unc = mood.uncertain_tracks(db, tmp_path, 5)
    assert len(unc) == 5 and all("uncertainty" in u for u in unc)
    assert unc[0]["uncertainty"] >= unc[-1]["uncertainty"]
    mood.predict(db, tmp_path)
    assert mood.next_step(db, tmp_path)["step"] == "review"


def seed_artists(db, n_artists=12, per=8, informative=False, seed=5):
    """Each artist has its own timbre signature (artist-specific offsets) and ONE mood.  With informative=False the
    mood has nothing to do with the sound, so only memorising the artist can score well."""
    rng = np.random.RandomState(seed)
    moods = ["Грустное", "Весёлое"]
    ids = []
    for a in range(n_artists):
        kind = "sad" if (a % 2) else "happy"
        sig = rng.normal(0, 1, 6)
        mood_name = moods[a % 2] if informative else moods[rng.randint(2)]
        for i in range(per):
            d = {n: None for n in DESCRIPTOR_NAMES}
            base = {"sad": (60, -22, 800), "happy": (140, -9, 3500)}[kind] if informative else (100, -15, 2000)
            d.update(bpm=base[0] + sig[0] * 15 + rng.normal(0, 2), loudness=base[1] + sig[1] * 3 + rng.normal(0, .5),
                     spectral_centroid=base[2] + sig[2] * 500 + rng.normal(0, 60), pitch=200 + sig[3] * 30 + rng.normal(0, 3),
                     dissonance=0.3 + sig[4] * 0.1 + rng.normal(0, .01), dynamic_range=10 + sig[5] * 2 + rng.normal(0, .3),
                     tonality="C major")
            tid = db.upsert_track({"path": f"a{a}_{i}.mp3", "title": f"t{i}", "artist": f"Artist {a}"}, [])
            db.set_descriptors(tid, d)
            db.set_moods([tid], [mood_name])
            ids.append(tid)
    return ids


def test_artist_grouped_validation_exposes_artist_memorising(db, tmp_path):
    seed_artists(db, n_artists=30, informative=False)       # mood is unrelated to sound: honest AUC must be ~0.5
    r = mood.train(db, tmp_path, epochs=60)                 # (30 artists: with 12 the honest AUC is pure noise, 0.1-0.8)
    assert r["grouped"] and r["n_groups"] == 30
    assert r["val"]["auc"] < 0.7                            # nothing real to learn
    assert r["auc_random_split"] > r["val"]["auc"] + 0.15   # a random split would have looked great (artist leakage)


def test_grouped_validation_still_finds_a_real_signal_and_picks_a_model(db, tmp_path):
    seed_artists(db, informative=True)
    seen = []
    r = mood.train(db, tmp_path, epochs=60, on_cv=seen.append)
    assert r["val"]["auc"] > 0.9 and r["chosen"] in r["cv"] and len(r["cv"]) == 7  # 6 logreg + MLP (descriptors only)
    assert seen and seen[-1][0]["auc"] >= seen[-1][-1]["auc"]            # leaderboard is sorted best-first
    assert r["val"]["top1"] > 0.85


def test_probabilities_are_calibrated_and_old_models_still_load(db, tmp_path):
    seed_artists(db, informative=True)
    r = mood.train(db, tmp_path, epochs=60)
    assert r["ece_calibrated"] <= r["ece_raw"] + 0.02
    m = mood.load_model(tmp_path)
    X = np.vstack([mood.model_row(db, m, t) for t in range(1, 5)])
    cal, raw = m.predict_proba(X), m.predict_proba(X, calibrated=False)
    assert cal.shape == raw.shape == (4, 2) and (cal >= 0).all() and (cal <= 1).all()
    m.platt = None                                          # a model saved before calibration existed
    assert np.allclose(m.predict_proba(X), raw)


def test_next_step_asks_for_clap_embeddings_before_training_when_clap_is_available(db, tmp_path, monkeypatch):
    from musicplayer.core import clap
    monkeypatch.setattr(clap, "available", lambda: True)
    ids = seed(db, 40)
    for tid, kind in ids[:24]:
        db.set_moods([tid], ["Грустное" if kind == "sad" else "Весёлое"])
    assert mood.next_step(db, tmp_path)["step"] == "clap_labeled"
    for tid, kind in ids[:24]:
        db.set_embedding(tid, "clap", np.ones(512, dtype=np.float32).tobytes())
    assert mood.next_step(db, tmp_path)["step"] == "train"
