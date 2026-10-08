import numpy as np

from musicplayer.core import langvocal
from musicplayer.core.descriptors import DESCRIPTOR_NAMES


def add(db, artist, title, album=""):
    return db.upsert_track({"path": f"{artist}-{title}.mp3", "title": title, "artist": artist, "album": album}, [])


def test_script_decides_when_unambiguous():
    assert langvocal.script_lang("Группа крови") == "ru" and langvocal.script_lang("雨上がりの家") == "ja"
    assert langvocal.script_lang("사랑") == "ko" and langvocal.script_lang("夜曲") == "zh"
    assert langvocal.script_lang("Yesterday") == "lat" and langvocal.script_lang("1234") is None


def test_latin_titles_follow_the_artist_and_overrides(db):
    a = [add(db, "Кино", t) for t in ("Звезда", "Пачка сигарет")]
    ru_latin = add(db, "Кино", "Final Song")                       # Latin title, but a Cyrillic-titled artist sings Russian
    en = [add(db, "Beatles", t) for t in ("Yesterday", "Help!")]
    over = add(db, "blinch", "Please Dont Minecraft Anything")      # in the Russian-artists override table
    jp = add(db, "Joe Hisaishi", "雨上がりの家")
    d = langvocal.detect_languages(db)
    assert d[a[0]] == "ru" and d[ru_latin] == "ru" and d[en[0]] == "en" and d[over] == "ru" and d[jp] == "ja"
    assert langvocal.detect_languages(db, {"Beatles": "other"})[en[0]] == "other"        # per-artist override wins


def test_user_language_and_vocal_are_never_overwritten_by_auto(db):
    t = add(db, "Beatles", "Yesterday")
    db.set_language([t], "ja")                                       # the user's choice (manual)
    langvocal.apply_languages(db)
    assert db.get_track(t)["lang"] == "ja"
    db.set_vocal([t], "inst")
    assert db.set_vocal([t], "voc", src="auto", only_auto=True) == 0 and db.get_track(t)["vocal"] == "inst"


def test_filters_russian_foreign_instrumental(db):
    ru = add(db, "Кино", "Звезда")
    en = add(db, "Beatles", "Yesterday")
    inst = add(db, "Eno", "Ambient 1")
    langvocal.apply_languages(db)
    db.set_vocal([ru, en], "voc"); db.set_vocal([inst], "inst")
    assert [t["id"] for t in db.search(lang="ru")] == [ru]
    assert {t["id"] for t in db.search(lang="foreign")} == {en, inst}
    assert [t["id"] for t in db.search(vocal="inst")] == [inst]
    assert [t["id"] for t in db.search(lang="foreign", vocal="voc")] == [en]


def test_vocal_classifier_learns_from_labels_and_predicts_the_rest(db, tmp_path):
    rng = np.random.RandomState(0)
    ids = []
    for i in range(40):
        inst = i % 4 == 0
        tid = add(db, f"Artist {i}", f"t{i}")
        d = {n: None for n in DESCRIPTOR_NAMES}
        d.update(bpm=100 + rng.normal(0, 10), loudness=-14 + rng.normal(0, 2),
                 spectral_centroid=(1200 if inst else 2800) + rng.normal(0, 150), pitch_confidence=(0.9 if inst else 0.4) + rng.normal(0, .05))
        db.set_descriptors(tid, d)
        db.set_embedding(tid, "clap", (rng.normal(0, 1, 512) + (0.5 if inst else 0.0) * np.ones(512)).astype("float32").tobytes())
        ids.append((tid, inst))
    for tid, inst in ids[:30]:
        db.set_vocal([tid], "inst" if inst else "voc", src="seed")
    info = langvocal.train_vocals(db, tmp_path)
    assert info["auc"] > 0.9 and info["accuracy"] > 0.85 and info["instrumental"] >= 5
    r = langvocal.predict_vocals(db, tmp_path)
    assert r["predicted"] == 10
    hits = sum((db.get_track(t)["vocal"] == "inst") == inst for t, inst in ids[30:])
    assert hits >= 9 and all(db.get_track(t)["vocal"] for t, _ in ids[:30])
