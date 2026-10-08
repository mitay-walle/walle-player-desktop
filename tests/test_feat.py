import pytest

from musicplayer.core.feat import parse
from musicplayer.core.scanner import scan


@pytest.mark.parametrize("title,artist,clean,feat", [
    ("Последний бал (feat. Ed-Win)", "Jane Air", "Последний бал", ["Ed-Win"]),
    ("Рок это кал(feat. Вася Васин)", "Noize MC", "Рок это кал", ["Вася Васин"]),
    ("Нероссия feat. Влади (Каста)", "Noize MC", "Нероссия", ["Влади (Каста)"]),
    ("Теряя Контроль (feat. Noek, Maria: Abort Chosen)", "Such", "Теряя Контроль", ["Noek", "Maria: Abort Chosen"]),
    ("Like A Rolling Stone (feat. Бендер и Денис, ПТВП)", "x", "Like A Rolling Stone", ["Бендер и Денис", "ПТВП"]),
    ("Где ты была?(ft. Rusted) (Rusted ragga-dance mix) 2012", "Рычаги машин",
     "Где ты была? (Rusted ragga-dance mix) 2012", ["Rusted"]),
    ("А это кто (при участии Вартика)", "Страна О.З", "А это кто", ["Вартика"]),
    ("Oceans (feat. Daniel Tompkins) (New!)", "Omertah", "Oceans (New!)", ["Daniel Tompkins"]),
    ("Song", "Main feat. Guest One, Guest Two", "Song", ["Guest One", "Guest Two"]),
    ("Время X", "Louna", "Время X", []),
    ("One‐X", "Three Days Grace", "One‐X", []),
    ("Soft", "Simon & Garfunkel", "Soft", []),
    ("Featured Snake", "Reptile", "Featured Snake", []),
    ("Draft shift", "Aft", "Draft shift", []),
])
def test_parse(title, artist, clean, feat):
    p = parse(title, artist)
    assert (p.title, p.feat) == (clean, feat)


def test_artist_tag_split():
    p = parse("Song", "Main feat. Guest")
    assert p.artists == ["Main"] and p.feat == ["Guest"]


def test_db_search_and_filters_by_featured_artist(db, tmp_path):
    from .conftest import make_mp3
    root = tmp_path / "lib"
    make_mp3(root / "Noize MC" / "М (feat. Марина Кацуба).mp3", artist="Noize MC", title="М (feat. Марина Кацуба)")
    make_mp3(root / "Noize MC" / "Другая.mp3", artist="Noize MC", title="Другая")
    make_mp3(root / "Кацуба" / "Своя.mp3", artist="Марина Кацуба", title="Своя")
    scan(db, root, tmp_path / "c")
    t = db.search("кацуба")                       # found as featured artist and as main artist
    assert len(t) == 2
    assert [x["title"] for x in db.search(artist="Марина Кацуба", order="title")] == ["М (feat. Марина Кацуба)", "Своя"]
    rows = db.search(artist="Noize MC")
    assert [r["feat"] for r in sorted(rows, key=lambda r: r["title"])] == [[], ["Марина Кацуба"]]
    assert db.list_values("feat_artist") == [{"name": "Марина Кацуба", "n": 1}]
    only = db.search(feat="Марина Кацуба")
    assert len(only) == 1 and only[0]["feat"] == ["Марина Кацуба"]


def test_existing_database_is_migrated(tmp_path):
    import sqlite3
    from musicplayer.core.db import Database
    p = tmp_path / "old.db"
    d = Database(p)
    d.upsert_track({"path": "a.mp3", "title": "X (feat. Guest)", "artist": "A"}, [])
    d.conn.execute("DELETE FROM track_artists")
    d.conn.execute("PRAGMA user_version=1")
    d.close()
    d2 = Database(p)                                    # reopening backfills feat for existing tracks
    assert d2.search(feat="Guest")[0]["title"] == "X (feat. Guest)"
