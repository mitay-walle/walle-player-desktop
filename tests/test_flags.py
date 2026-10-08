import pytest

from musicplayer.core import flags, mood

from .test_mood import seed


@pytest.mark.parametrize("title,album,expected", [
    ("Уходи (Acoustic Live)", "Перезвони мне", {"live", "acoustic"}),
    ("На заре Arena Riga Live", "", {"live"}),
    ("be healthy (demo)", "", {"demo"}),
    ("Утки (Bonus)", "Журналы", set()),
    ("So Sick (BBC Radio 1 - Live Lounge)", "", {"live"}),
    ("Не плачь, подруга (акустика)", "", {"acoustic"}),
    ("Beautiful (Remix)", "", {"remix"}),
    ("Help! (The Beatles cover)", "", {"cover"}),
    ("Концерт в Москве", "", {"live"}),
    ("we'll live", "i clung to you", set()),                     # the word "live" as a verb is not a live recording
    ("live up", "", set()),
    ("Весна", "Illusions Live - Viretta Park", {"live"}),
    ("Обычная песня", "Обычный альбом", set()),
    ("Turn Up the Music", "", set()),                              # "turn" is not a live marker
    ("Live at Wembley", "", {"live"}),
    ("Hello (Unplugged)", "", {"live", "acoustic"}),
    ("Acoustic", "", {"acoustic"}),
])
def test_detect(title, album, expected):
    assert flags.detect(title, album) == expected


def add(db, title, album=""):
    return db.upsert_track({"path": f"{title}.mp3", "title": title, "artist": "a", "album": album}, [])


def test_live_and_acoustic_are_separate_flags():
    assert flags.detect("Song (Acoustic Live)") == {"live", "acoustic"}       # both, independently
    assert flags.detect("Song (Acoustic)") == {"acoustic"} and flags.detect("Song (Live)") == {"live"}


def test_auto_flags_manual_edits_freeze_and_filters(db):
    live = add(db, "Song (live)")
    ac = add(db, "Song (acoustic)")
    studio = add(db, "Song")
    r = flags.apply_detected(db)
    assert r["counts"] == {"live": 1, "acoustic": 1}
    assert db.get_track(live)["flags"] == ["live"]
    db.set_flags([studio], ["demo"], "add")                      # manual edit
    db.set_flags([live], ["live"], "remove")
    flags.apply_detected(db)                                      # a second automatic pass must not undo the user's edits
    assert db.get_track(live)["flags"] == [] and db.get_track(studio)["flags"] == ["demo"]
    assert {t["id"] for t in db.search(flag="acoustic")} == {ac}
    assert {t["id"] for t in db.search(exclude_flags=["live", "demo"])} == {live, ac}
    assert db.get_track(studio)["flags"] == ["demo"]


def test_poor_sound_recordings_are_left_out_of_training(db):
    ids = seed(db, 40)
    for tid, kind in ids[:30]:
        db.set_moods([tid], ["Грустное" if kind == "sad" else "Весёлое"])
        db.conn.execute("UPDATE track_moods SET source='seed' WHERE track_id=?", (tid,))
    full = len(mood.training_matrix(db)[0])
    live = ids[0][0]
    db.set_flags([live], ["live"], "add")
    assert len(mood.training_matrix(db)[0]) == full - 1          # a live seed-labelled track is dropped
    db.set_moods([live], ["Грустное"])                            # ... but a hand-labelled (manual) one is kept
    assert len(mood.training_matrix(db)[0]) == full
