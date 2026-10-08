import os
import time

from musicplayer.core.scanner import scan
from .conftest import make_mp3


def test_scan_reads_tags_unicode_and_cover(db, library, tmp_path):
    r = scan(db, library, tmp_path / "covers")
    assert (r["added"], r["total"], r["errors"]) == (3, 3, [])
    rows = db.search(artist="Кино")
    assert [t["title"] for t in rows] == ["Группа крови", "Закрой за мной дверь"]
    first = rows[0]
    assert first["year"] == 1988 and first["track_no"] == 1 and first["album"] == "Группа крови"
    assert first["genres"] == ["Rock", "Post-Punk"]
    assert first["cover_key"] and (tmp_path / "covers" / f"{first['cover_key']}.jpg").exists()
    assert rows[1]["cover_key"] is None


def test_single_track_artist_from_folder(db, tmp_path):
    p = make_mp3(tmp_path / "Артист" / "Песня.mp3")  # no tags at all
    scan(db, tmp_path, tmp_path / "c")
    t = db.search()[0]
    assert t["title"] == "Песня" and t["artist"] == "Артист"


def test_incremental_rescan(db, library, tmp_path):
    scan(db, library, tmp_path / "c")
    r = scan(db, library, tmp_path / "c")
    assert (r["added"], r["updated"]) == (0, 0)
    f = library / "Beatles" / "Yesterday.mp3"
    make_mp3(f, artist="Beatles", title="Yesterday (remaster)")
    os.utime(f, (time.time() + 10, time.time() + 10))
    r = scan(db, library, tmp_path / "c")
    assert (r["added"], r["updated"]) == (0, 1)
    assert db.search("remaster")[0]["title"] == "Yesterday (remaster)"


def test_deleted_file_marked_missing(db, library, tmp_path):
    scan(db, library, tmp_path / "c")
    (library / "Beatles" / "Yesterday.mp3").unlink()
    r = scan(db, library, tmp_path / "c")
    assert r["missing"] == 1
    assert db.search("yesterday") == []


def test_edited_genres_survive_rescan_unless_file_changes(db, library, tmp_path):
    scan(db, library, tmp_path / "c")
    tid = db.search(artist="Beatles")[0]["id"]
    db.set_genres([tid], ["Oldies"])
    scan(db, library, tmp_path / "c", full=True)
    assert db.get_track(tid)["genres"] == ["Oldies"]
    assert db.genre_changes()[0]["db"] == ["Oldies"]
    f = library / "Beatles" / "Yesterday.mp3"
    make_mp3(f, artist="Beatles", title="Yesterday", genre=["Rock"])
    scan(db, library, tmp_path / "c", full=True)
    assert db.get_track(tid)["genres"] == ["Rock"]


def test_search_prefix_cyrillic_and_filters(db, library, tmp_path):
    scan(db, library, tmp_path / "c")
    assert len(db.search("кин")) == 2
    assert len(db.search("rock")) == 2          # genre is indexed
    assert len(db.search(genre="pop")) == 1
    assert len(db.search(year_from=1980)) == 2
    assert db.count(not_played_days=30) == 3


def test_moods_suggestions_flow(db, library, tmp_path):
    scan(db, library, tmp_path / "c")
    a, b = [t["id"] for t in db.search(artist="Кино")]
    db.set_moods([a], ["Грустное", "Ностальгия"])
    assert db.suggest_moods([(a, "Весёлое", 0.9), (b, "Грустное", 0.8)], "model") == 2
    assert db.get_track(a)["moods"] == ["Грустное", "Ностальгия"]        # confirmed only
    assert db.get_track(b)["mood_suggestions"][0]["confidence"] == 0.8
    assert db.search(mood="Грустное") == db.search(mood="грустное") and len(db.search(mood="Грустное")) == 1
    assert len(db.search(mood="Грустное", mood_status="any")) == 2
    assert db.search(no_mood=True, artist="Кино")[0]["id"] == b
    db.resolve_suggestions([b], accept=True)
    assert db.get_track(b)["moods"] == ["Грустное"]
    db.resolve_suggestions([a], accept=False)
    assert db.get_track(a)["mood_suggestions"] == []
    assert sorted(t for _, ms in db.mood_training_set() for t in ms).count("Грустное") == 2
    assert len(db.search("грустное")) == 2  # confirmed moods are searchable


def test_similar_by_tags(db, library, tmp_path):
    from musicplayer.core.similar import similar_tracks
    scan(db, library, tmp_path / "c")
    a = db.search(title="Группа")[0] if False else db.search("группа")[0]
    res = similar_tracks(db, a["id"])
    assert res[0]["title"] == "Закрой за мной дверь" and res[0]["basis"] == "tags"


def test_scan_refuses_unavailable_root_and_keeps_catalogue(db, library, tmp_path):
    import shutil

    import pytest
    scan(db, library, tmp_path / "c")
    with pytest.raises(FileNotFoundError):
        scan(db, tmp_path / "unplugged", tmp_path / "c")
    for f in list(library.rglob("*.mp3")):          # folder still exists but is empty (drive glitch)
        f.rename(f.with_suffix(".bak"))
    with pytest.raises(RuntimeError, match="диск недоступен"):
        scan(db, library, tmp_path / "c")
    assert db.count() == 3                           # nothing was marked missing


def test_two_connections_writing_the_same_file_wait_instead_of_failing(tmp_path):
    """A background job and the GUI share one catalogue; a busy writer must make the other wait, not raise 'locked'."""
    import threading
    import time

    from musicplayer.core.db import Database
    path = tmp_path / "shared.db"
    a, b = Database(path), Database(path)
    a.conn.execute("BEGIN IMMEDIATE")
    a.upsert_track({"path": "x.mp3", "title": "x", "artist": "a"}, [])
    result = {}

    def writer():
        t0 = time.time()
        b.upsert_track({"path": "y.mp3", "title": "y", "artist": "b"}, [])
        result["waited"] = time.time() - t0
    th = threading.Thread(target=writer)
    th.start()
    time.sleep(1.0)
    a.conn.execute("COMMIT")                          # release the lock after a second
    th.join(10)
    assert result.get("waited", 0) >= 0.8 and a.count() == 2


def test_labels_export_import_roundtrip_by_path(db, library, tmp_path, monkeypatch):
    import importlib.util
    import json
    spec = importlib.util.spec_from_file_location("labels_io", __import__("pathlib").Path(__file__).resolve().parent.parent / "scripts" / "labels_io.py")
    lio = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lio)
    scan(db, library, tmp_path / "c")
    a, b, c = [t["id"] for t in db.search(order="title")]
    db.set_moods([a], ["Грустное", "Рейв"])
    db.conn.execute("UPDATE track_moods SET source='seed' WHERE track_id=?", (a,))
    db.set_moods([b], ["Весёлое"])                                  # manual
    db.suggest_moods([(c, "Спокойное", 0.9)], "auto")                # automatic: must NOT be exported
    db.set_vocal([a], "inst", src="seed")
    db.set_language([b], "ja")
    monkeypatch.setattr(lio, "Database", lambda p: db)
    out = tmp_path / "labels.json"
    lio.export(out)
    data = json.loads(out.read_text(encoding="utf-8"))
    pa, pb, pc = (db.get_track(i)["path"] for i in (a, b, c))
    assert set(data["moods"]) == {pa, pb} and data["moods"][pb]["src"] == "manual" and data["moods"][pa]["src"] == "seed"
    assert data["vocal"] == {pa: "inst"} and data["lang"] == {pb: "ja"}
    db.conn.execute("DELETE FROM track_moods")                       # lose the labels, then restore them from the file
    lio.import_(out)
    assert set(db.get_track(a)["moods"]) == {"Грустное", "Рейв"} and db.get_track(b)["mood_sources"] == {"Весёлое": "manual"}
    assert db.get_track(c)["moods"] == [] and db.get_track(a)["vocal"] == "inst"
