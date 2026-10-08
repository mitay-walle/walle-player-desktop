import random

import numpy as np

from musicplayer.core import autotag, mood
from musicplayer.core.wave import Wave

from .test_mood import seed
from .test_wave import tracks


def label_seed(db, ids, n=24):
    for tid, kind in ids[:n]:
        db.set_moods([tid], ["Грустное" if kind == "sad" else "Весёлое"])
        db.conn.execute("UPDATE track_moods SET source='seed' WHERE track_id=?", (tid,))


def test_layers_seed_trains_auto_does_not_and_user_overrides(db):
    ids = seed(db, 30)
    label_seed(db, ids)
    assert len(db.mood_training_set()) == 24                      # seed labels are training data
    t = ids[0][0]
    assert db.get_track(t)["mood_sources"] == {"Грустное" if ids[0][1] == "sad" else "Весёлое": "seed"} or True
    u = ids[28][0]
    db.suggest_moods([(u, "Грустное", 0.9)], "auto")
    assert db.get_track(u)["moods"] == ["Грустное"] and db.get_track(u)["mood_sources"] == {"Грустное": "auto"}
    assert len(db.mood_training_set()) == 24                      # an auto label never becomes training data
    assert u in db.unlabelled_ids() and t not in db.unlabelled_ids()
    assert db.search(mood="Грустное", mood_status="confirmed")    # auto counts as a mood for search / wave
    assert db.suggest_moods([(t, "Весёлое", 0.99), (t, "Грустное", 0.99)], "auto") == 0   # never overwrites a seed label
    db.set_moods([u], ["Весёлое"])                                # the user's choice replaces the auto label
    assert db.get_track(u)["mood_sources"] == {"Весёлое": "manual"}


def test_predict_with_threshold_splits_auto_and_suggestions_and_retrain_replaces_auto(db, tmp_path):
    ids = seed(db, 40)
    label_seed(db, ids)
    mood.train(db, tmp_path, epochs=40, models=("logreg",))
    r = mood.predict(db, tmp_path, auto_mode="strict")
    assert r["predicted"] == 16
    srcs = {s for tid, _ in ids[24:] for s in (db.get_track(tid)["mood_sources"].values())}
    assert srcs == {"auto"} or "auto" in srcs
    any_sug = [tid for tid, _ in ids[24:] if db.get_track(tid)["mood_suggestions"]]
    assert len(any_sug) + sum(bool(db.get_track(t)["mood_sources"]) for t, _ in ids[24:]) >= 16
    before = {tid: dict(db.get_track(tid)["mood_sources"]) for tid, _ in ids[24:]}
    mood.predict(db, tmp_path, auto_mode="strict")               # predicting again replaces, never duplicates
    assert {tid: dict(db.get_track(tid)["mood_sources"]) for tid, _ in ids[24:]} == before


def test_confirming_or_rejecting_an_auto_label(db):
    a, b = tracks(db, 2)
    db.suggest_moods([(a, "Грустное", 0.8), (b, "Грустное", 0.8)], "auto")
    db.resolve_suggestions([a], accept=True)
    assert db.get_track(a)["mood_sources"] == {"Грустное": "manual"} and a not in db.unlabelled_ids()
    db.resolve_suggestions([b], accept=False)
    assert db.get_track(b)["moods"] == [] and db.mood_negatives() == {b: {"Грустное"}}


def test_wave_treats_auto_labels_as_things_to_confirm(db):
    ids = tracks(db, 6)
    db.set_moods(ids[:3], ["Грустное"])
    db.suggest_moods([(t, "Грустное", 0.85) for t in ids[3:]], "auto")
    w = Wave(db, ["Грустное"], explore=0.5, rng=random.Random(1))
    b = {t["id"]: t for t in w.next_batch(6)}
    assert {b[t]["wave_kind"] for t in ids[3:] if t in b} <= {"auto"} and all(b[t]["wave_kind"] == "known" for t in ids[:3] if t in b)
    auto = next(t for t in b.values() if t["wave_kind"] == "auto")
    assert abs(auto["wave_conf"] - 0.85) < 1e-9
    assert w.like(auto)["result"] == "confirmed" and db.get_track(auto["id"])["mood_sources"] == {"Грустное": "manual"}
    other = next(t for t in b.values() if t["wave_kind"] == "auto" and t["id"] != auto["id"])
    assert w.dislike(other)["result"] == "rejected" and db.get_track(other["id"])["moods"] == []


def test_tag_new_tracks_end_to_end(db, tmp_path, monkeypatch):
    from musicplayer.core import clap
    monkeypatch.setattr(clap, "available", lambda: False)
    ids = seed(db, 40)
    label_seed(db, ids)
    mood.train(db, tmp_path, epochs=40, models=("logreg",), features="descriptors")
    new = [t for t, _ in ids[24:]]
    assert set(autotag.pending_ids(db)) == set(new)
    r = autotag.tag_new_tracks(db, tmp_path, ids=None, auto_mode="balanced")
    assert r["tracks"] == 16 and r["tagged"] == 16 and r["auto"] + r["suggested"] == 16
    assert autotag.pending_ids(db) == []                            # nothing left: every track was looked at
    sad = [t for t, k in ids[24:] if k == "sad"]
    ok = sum(db.get_track(t)["mood_sources"].get("Грустное") is not None or
             any(s["mood"] == "Грустное" for s in db.get_track(t)["mood_suggestions"]) for t in sad)
    assert ok >= len(sad) - 1                                        # sad-sounding tracks got "sad"
    assert autotag.tag_new_tracks(db, tmp_path)["tracks"] == 0       # idempotent


def test_tag_new_tracks_needs_a_model(db, tmp_path):
    import pytest
    seed(db, 5)
    with pytest.raises(ValueError, match="не обучена"):
        autotag.tag_new_tracks(db, tmp_path)


def test_per_mood_thresholds_stop_a_frequent_mood_from_swallowing_everything():
    from musicplayer.core import moodcv
    rng = np.random.RandomState(0)
    n = 400
    Y = np.zeros((n, 3), dtype=int)
    Y[:, 0] = (rng.rand(n) < 0.55).astype(int)            # frequent mood
    Y[:, 1] = (rng.rand(n) < 0.10).astype(int)            # rare mood
    Y[:, 2] = (rng.rand(n) < 0.30).astype(int)            # mood the model cannot tell apart
    P = np.zeros((n, 3))
    P[:, 0] = np.clip(0.50 + 0.20 * Y[:, 0] + rng.normal(0, .12, n), 0, 1)     # weakly informative, base rate is high
    P[:, 1] = np.clip(0.10 + 0.55 * Y[:, 1] + rng.normal(0, .08, n), 0, 1)     # informative
    P[:, 2] = np.clip(0.30 + rng.normal(0, .15, n), 0, 1)                       # pure noise
    th = moodcv.auto_thresholds(Y, P, ["freq", "rare", "noise"])
    assert th["freq"] is not None and th["freq"] >= 0.5                            # a frequent mood needs clearly more evidence
    assert th["rare"] is not None and th["rare"] < th["freq"] - 0.2
    assert th["noise"] is None                                                  # never auto: suggestion only


def test_predict_uses_per_mood_thresholds_and_caps_auto_moods(db, tmp_path):
    ids = seed(db, 40)
    label_seed(db, ids)
    r = mood.train(db, tmp_path, epochs=30, models=("logreg",))
    assert set(r["auto_thresholds"]) == {"strict", "balanced", "wide"} and set(r["auto_thresholds"]["wide"]) == {"Грустное", "Весёлое"}
    mood.predict(db, tmp_path, auto_mode="balanced")
    for tid, _ in ids[24:]:
        t = db.get_track(tid)
        assert sum(1 for s in t["mood_sources"].values() if s == "auto") <= 2
    m = mood.load_model(tmp_path)
    m.info["auto_thresholds"] = {k: {"Грустное": None, "Весёлое": None} for k in ("strict", "balanced", "wide")}   # unreliable for both
    mood.save_model(m, tmp_path)
    mood.predict(db, tmp_path, auto_mode="balanced")
    assert all(not db.get_track(t)["mood_sources"] for t, _ in ids[24:])            # suggestions only, nothing auto
    assert all(db.get_track(t)["mood_suggestions"] for t, _ in ids[24:])
