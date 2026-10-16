"""The genre vocabulary in English: Russian names are aliases, glued tags are split, the user's genres join the tree."""

import sqlite3

from musicplayer.core.genres import normalize, search_text
from tests.test_remote import client, lib  # noqa: F401  (server fixtures)


def test_russian_names_and_spellings_map_to_one_english_genre():
    assert normalize(["Хип-хоп", "Русский рэп", "rap"]) == ["Hip-Hop", "Russian Rap"]
    assert normalize(["Электроника", "Électronique", "Электронная музыка", "Electronics"]) == ["Electronic"]
    assert normalize(["Дезкор", "Экстремальный Метал", "Метал"]) == ["Metal", "Deathcore"]       # no duplicates
    assert normalize(["JRock"]) == ["Rock", "J-Rock"] and normalize(["Other"]) == []


def test_glued_tags_are_split_and_every_piece_finds_its_family():
    assert normalize(["Post-Hardcore | Pop-Punk"]) == ["Metal", "Post-Hardcore", "Punk", "Pop Punk"]
    assert normalize(["Alternative Rock, Post Grunge"]) == ["Rock", "Alternative", "Grunge"]
    assert normalize(["Synthwave, Dreamwave, Retrowave, Outrun, Electronic"]) == ["Electronic", "Synthwave"]
    assert normalize(["Folk / Rock", "Rock/Pop"]) == ["Folk", "Rock", "Pop"]


def test_search_text_has_both_languages():
    assert search_text(["Rock", "Pop Punk"]) == "Rock Рок Pop Punk Поп-панк"


def test_old_catalogue_is_translated(tmp_path):
    """v15 catalogue with Russian names and the user's own Russian genres -> English, nothing left outside the tree."""
    from musicplayer.core import genres
    from musicplayer.core.db import Database
    path = tmp_path / "old.db"
    db = Database(path)
    tid = db.upsert_track({"path": "a.mp3", "title": "a", "artist": "Breakdown of Sanity"}, [])
    db.conn.executemany("INSERT INTO track_genres VALUES(?,?)",
                        [(tid, g) for g in ("Метал", "Дезкор", "Экстремальный Метал", "Post-Hardcore | Pop-Punk")])
    db.conn.executemany("INSERT INTO custom_genres VALUES(?,?,?)",
                        [("Дезкор", None, 1.0), ("Экстремальный Метал", None, 2.0), ("Сладж", "Метал", 3.0)])
    db.conn.execute("PRAGMA user_version=15")
    db.conn.commit()
    db.close()
    db = Database(path)
    assert db.get_track(tid)["genres"] == ["Metal", "Deathcore", "Post-Hardcore", "Punk", "Pop Punk"]
    assert db.conn.execute("SELECT count(*) FROM custom_genres").fetchone()[0] == 0     # built in now, or Russian
    assert "Сладж" not in genres.PARENT
    db.close()
    Database()                                                                # reset the shared tree
    assert isinstance(sqlite3.version, str)


def test_russian_is_never_stored_and_typing_it_is_refused(tmp_path):
    import pytest

    from musicplayer.core import genres
    from musicplayer.core.db import Database
    assert normalize(["Сладж", "Шансон", "Русский рок", "Поп-панк"]) == ["Rock", "Punk", "Pop Punk"]   # files: known ones
    with pytest.raises(ValueError, match="по-английски.*Поп-панк → Pop Punk"):
        genres.check_latin(["Поп-панк"])
    db = Database(tmp_path / "a.db")
    with pytest.raises(ValueError):
        genres.learn(db, "Punk/Скейт-панк")
    assert genres.learn(db, "Punk/Crust Punk") == ["Crust Punk"]
    db.close()
    Database()                                                                # reset the shared tree


def test_server_refuses_russian_genres(lib, client):
    from tests.test_remote import AUTH
    tid = lib.ids[0]
    for url, body in ((f"/api/tracks/{tid}/genres", {"genres": ["Поп-панк"]}),
                      (f"/api/tracks/{tid}/labels", {"genres": ["Punk/Скейт-панк"]})):
        r = client.post(url, json=body, headers=AUTH)
        assert r.status_code == 400 and "по-английски" in r.json()["error"]
    assert client.post(f"/api/tracks/{tid}/genres", json={"genres": ["Pop Punk"]}, headers=AUTH).json()["genres"] == [
        "Punk", "Pop Punk"]
