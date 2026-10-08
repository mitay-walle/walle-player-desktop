import numpy as np
import pytest
import soundfile as sf

from musicplayer.core import clap, mood
from musicplayer.core.descriptors import DESCRIPTOR_NAMES

from .test_mood import fake_desc, seed

RNG = np.random.RandomState(3)
PROTO = {"Грустное": RNG.randn(512), "Весёлое": RNG.randn(512)}


def unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


def emb_for(kind):
    base = PROTO["Грустное" if kind == "sad" else "Весёлое"]
    return unit(base + RNG.randn(512) * 0.6)


@pytest.fixture
def fake_clap(db, monkeypatch):
    """Tracks with descriptors AND fake CLAP embeddings, and fake text embeddings for two moods."""
    ids = seed(db, 40)
    for tid, kind in ids:
        db.set_embedding(tid, "clap", emb_for(kind).tobytes())
    monkeypatch.setattr(clap, "text_embeddings", lambda d, cache, names=None: {k: unit(v) for k, v in PROTO.items()})
    return ids


def test_zero_shot_needs_no_labels_and_never_confirms(db, tmp_path, fake_clap):
    r = clap.zero_shot(db, tmp_path)
    assert r["predicted"] == 40
    ok = 0
    for tid, kind in fake_clap:
        t = db.get_track(tid)
        assert t["moods"] == [] and t["mood_suggestions"][0]["source"] == "clap"
        ok += t["mood_suggestions"][0]["mood"] == ("Грустное" if kind == "sad" else "Весёлое")
    assert ok >= 36


def test_agreement_with_own_labels(db, tmp_path, fake_clap):
    for tid, kind in fake_clap[:20]:
        db.set_moods([tid], ["Грустное" if kind == "sad" else "Весёлое"])
    a = clap.agreement(db, tmp_path)
    assert a["tracks"] == 20 and a["accuracy"] >= 0.9 and set(a["per_mood_recall"]) == {"Грустное", "Весёлое"}
    assert a["chance"] == 0.5 and a["accuracy_all_moods"] >= 0.9 and set(a["confusion"]) == {"Грустное", "Весёлое"}


def test_feature_modes_and_auto(db, tmp_path, fake_clap):
    for tid, kind in fake_clap[:30]:
        db.set_moods([tid], ["Грустное" if kind == "sad" else "Весёлое"])
    assert mood.resolve_mode(db, "auto") == "both"
    assert mood.row_for(db, fake_clap[0][0], "clap").shape == (512,)
    assert mood.row_for(db, fake_clap[0][0], "both").shape[0] == 512 + len(mood.FEATURE_NAMES)
    for mode in ("clap", "both", "descriptors"):
        res = mood.train(db, tmp_path, epochs=40, features=mode)
        assert res["mode"] == mode and res["val"]["auc"] > 0.8
    mood.train(db, tmp_path, epochs=40)               # auto -> both
    out = mood.predict(db, tmp_path)
    assert out["features"] == "both" and out["predicted"] == 10
    t = db.get_track(fake_clap[35][0])
    assert t["mood_suggestions"][0]["source"] == "model"


def test_clap_embeddings_make_similar_tracks_audio_based(db, tmp_path, fake_clap):
    from musicplayer.core.similar import similar_tracks
    sad = [tid for tid, k in fake_clap if k == "sad"]
    res = similar_tracks(db, sad[0], limit=10)
    assert res[0]["basis"] == "audio+tags"
    assert sum(r["id"] in sad for r in res[:5]) >= 4


def test_default_mood_prompts_are_filled(db):
    prompts = {m["name"]: m["prompt"] for m in db.list_moods()}
    assert "sad" in prompts["Грустное"] and all(prompts.values())


@pytest.mark.skipif(not clap.available(), reason="no python with torch+transformers for CLAP")
def test_real_worker_roundtrip(db, tmp_path):
    """Real subprocess + real CLAP model on generated sounds: a low sine vs. noise bursts."""
    ids = []
    for name, sig in (("low", 0.3 * np.sin(2 * np.pi * 110 * np.arange(48000 * 6) / 48000)),
                      ("noise", 0.3 * np.random.RandomState(0).randn(48000 * 6))):
        p = tmp_path / f"{name}.wav"
        sf.write(p, sig.astype("float32"), 48000)
        ids.append(db.upsert_track({"path": str(p), "title": name, "artist": "t"}, []))
    r = clap.embed_tracks(db, ids)
    assert r["embedded"] == 2 and not r["failed"]
    e = [np.frombuffer(db.get_embedding(i, "clap"), dtype=np.float32) for i in ids]
    assert e[0].shape == (512,) and abs(np.linalg.norm(e[0]) - 1) < 1e-3 and float(e[0] @ e[1]) < 0.99
    t = clap.text_embeddings(db, tmp_path, ["Грустное", "Спокойное"])
    assert set(t) == {"Грустное", "Спокойное"} and t["Грустное"].shape == (512,)
    assert (tmp_path / "clap_text.json").exists()
    z = clap.zero_shot(db, tmp_path, ids, threshold=0.0)
    assert z["predicted"] == 2


def test_auto_prefers_clap_when_it_covers_much_more_of_the_library(db, tmp_path, fake_clap):
    for tid, kind in fake_clap[:30]:
        db.set_moods([tid], ["Грустное" if kind == "sad" else "Весёлое"])
    assert mood.resolve_mode(db, "auto") == "both"
    for i in range(80):                                   # many more tracks with embeddings only
        tid = db.upsert_track({"path": f"x{i}.mp3", "title": f"x{i}", "artist": "z"}, [])
        db.set_embedding(tid, "clap", emb_for("sad").tobytes())
    assert mood.resolve_mode(db, "auto") == "clap"
    mood.train(db, tmp_path, epochs=30)                   # auto -> clap
    assert mood.predict(db, tmp_path)["predicted"] >= 80


def test_availability_requires_the_model_in_a_cache(monkeypatch, tmp_path):
    monkeypatch.delenv("HF_HOME", raising=False)
    monkeypatch.setenv("MUSICPLAYER_HF_HOME", str(tmp_path))
    monkeypatch.setattr(clap, "hf_roots", lambda: [tmp_path])
    assert not clap.model_cached() and clap.hf_home() is None
    snap = tmp_path / "hub" / "models--laion--clap-htsat-fused" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    assert not clap.model_cached()                       # an empty snapshot folder = not downloaded
    (snap / "config.json").write_text("{}")
    assert clap.model_cached() and clap.hf_home() == tmp_path


def test_clap_gui_workspace_cache_is_searched():
    roots = [str(r) for r in clap.hf_roots()]
    assert any(r.replace("\\", "/").endswith("CLAP_GUI/workspace/hf_cache") for r in roots)
