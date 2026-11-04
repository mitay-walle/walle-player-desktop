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


@pytest.mark.parametrize("title,artist,clean,feat", [
    ("Blow (with Bruno Mars & Chris Stapleton)", "Ed Sheeran", "Blow", ["Bruno Mars", "Chris Stapleton"]),
    ("Time Tides (With C.J. Catalizer)", "Astropilot", "Time Tides", ["C.J. Catalizer"]),
    ("I Wanna Secret Family (With You)", "The Offspring", "I Wanna Secret Family", []),
    ("Mamacita with Burrito", "Little Big", "Mamacita with Burrito", []),
    ('Transilvanian Cunthunger "feat. One Morning Left"', "Electric Callboy", "Transilvanian Cunthunger",
     ["One Morning Left"]),
    ("Somebody that I used to know (Gotye feat Kimbra cover)", "Cause I Can", "Somebody that I used to know (Gotye)",
     ["Kimbra"]),
    ("X (feat. Chris Cornell (Live From Projekt Revolution 2008))", "a", "X", ["Chris Cornell"]),
    ("Y feat. Джиган - Отпусти", "a", "Y", ["Джиган"]),
    ("Z (feat. Ñåðãåé ×ÈÆ ×èãðàêîâ)", "a", "Z", ["Сергей ЧИЖ Чиграков"]),
    ("Song (feat. Ann Lee) (with Bob Marley)", "x", "Song", ["Ann Lee", "Bob Marley"]),
])
def test_parse_more_forms(title, artist, clean, feat):
    p = parse(title, artist)
    assert (p.title, p.feat) == (clean, feat)


def test_split_collab():
    from musicplayer.core.feat import fix_mojibake, split_collab
    assert split_collab("Horus, SharOn") == ["Horus", "SharOn"]
    assert split_collab("Павел Пиковский и группа «Хьюго»") == ["Павел Пиковский", "группа «Хьюго»"]
    assert split_collab("Коmatozz vs. Супермаркет") == ["Коmatozz", "Супермаркет"]
    assert split_collab("Король и Шут") == ["Король и Шут"]
    assert split_collab("1,5 кг отличного пюре") == ["1,5 кг отличного пюре"]
    assert split_collab("HUNTR/X") == ["HUNTR/X"]
    assert fix_mojibake("Beyoncé") == "Beyoncé" and fix_mojibake("Motörhead") == "Motörhead"


def _add(db, i, title, artist, albumartist=None):
    d = {"path": f"t{i}.mp3", "title": title, "artist": artist}
    if albumartist:
        d["albumartist"] = albumartist
    return db.upsert_track(d, [])


def test_collaboration_is_listed_under_each_artist_that_is_known(db):
    _add(db, 0, "own 1", "Horus")
    _add(db, 1, "own 2", "Horus")
    t = _add(db, 2, "duet", "Horus, SharOn")                  # Horus is an artist of the library -> a collaboration
    _add(db, 3, "band", "Король и Шут")                       # nobody known -> one act
    _add(db, 4, "whole", "Simon & Garfunkel")
    db.rebuild_artist_links()
    names = {r["name"]: r["n"] for r in db.list_values("artist")}
    assert names["Horus"] == 3 and names["SharOn"] == 1
    assert "Horus, SharOn" not in names
    assert names["Король и Шут"] == 1 and names["Simon & Garfunkel"] == 1
    assert [x["title"] for x in db.search(artist="SharOn")] == ["duet"]
    assert {x["title"] for x in db.search(artist="Horus")} == {"own 1", "own 2", "duet"}
    assert db.get_track(t)["artist"] == "Horus, SharOn"       # the tag itself is not touched
    over = {r["artist"]: r["tracks"] for r in db.artists_overview()}
    assert over["Horus"] == 3 and over["SharOn"] == 1 and "Horus, SharOn" not in over


def test_featured_artist_is_listed_with_the_track_and_counted(db):
    _add(db, 0, "Solo", "Noize MC")
    _add(db, 1, "Duet (feat. Марина Кацуба)", "Noize MC")
    _add(db, 2, "Own", "Марина Кацуба")
    names = {r["name"]: r["n"] for r in db.list_values("artist")}
    assert names == {"Noize MC": 2, "Марина Кацуба": 2}
    hidden = {r["name"]: r["n"] for r in db.list_values("artist", "owner")}
    assert hidden == names
    over = {r["artist"]: r["tracks"] for r in db.artists_overview()}
    assert over == names
    only_guest = _add(db, 3, "Guest only (with Ed-Win)", "Jane Air")
    assert {r["name"] for r in db.list_values("artist")} >= {"Ed-Win", "Jane Air"}
    assert db.get_track(only_guest)["feat"] == ["Ed-Win"]


def test_edit_to_a_collaboration_and_back(db):
    _add(db, 0, "own", "Horus")
    t = _add(db, 1, "duet", "SharOn")
    assert "SharOn" in {r["name"] for r in db.list_values("artist")}
    db.edit_track([t], {"artist": "Horus, SharOn"})
    assert {r["name"]: r["n"] for r in db.list_values("artist")} == {"Horus": 2, "SharOn": 1}
    db.edit_track([t], {"artist": "SharOn"})
    assert {r["name"]: r["n"] for r in db.list_values("artist")} == {"Horus": 1, "SharOn": 1}


def test_one_letter_artist_is_a_feat():
    assert parse("x (feat. N)", "A").feat == ["N"]
    assert parse("x (feat. 7)", "A").feat == []
