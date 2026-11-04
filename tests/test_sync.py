"""Two-way sync of the catalogue between the library server and a desktop copy (core/sync.py)."""
import shutil
import time

import pytest

from musicplayer.core import likes, sync
from musicplayer.core.db import Database
from musicplayer.core.scanner import scan

from .conftest import make_mp3


@pytest.fixture
def pair(tmp_path, library):
    """The server's catalogue and a desktop copy of it (as after moving the server), both journaling."""
    a = Database(tmp_path / "server.db")
    scan(a, library, tmp_path / "covers")
    a.close()
    shutil.copy(tmp_path / "server.db", tmp_path / "desk.db")
    server, desk = Database(tmp_path / "server.db"), Database(tmp_path / "desk.db")
    sync.enable(server.conn)
    sync.enable(desk.conn)
    yield server, desk
    server.close()
    desk.close()


def exchange(server, desk, map_path=None) -> tuple[dict, dict]:
    """What the desktop's sync client does over HTTP: push its own entries, pull everyone else's."""
    me = sync.node(desk.conn)
    out, last = sync.entries_since(desk.conn, int(sync.meta(desk.conn, "pushed", "0")), origin=me)
    pushed = sync.apply(server, out, accept_new_tracks=False)
    sync.set_meta(desk.conn, "pushed", last)
    inc, last = sync.entries_since(server.conn, int(sync.meta(desk.conn, "pulled", "0")), exclude_origin=me)
    pulled = sync.apply(desk, inc, map_path=map_path)
    sync.set_meta(desk.conn, "pulled", last)
    return pushed, pulled


def tid_of(db, title):
    return db.conn.execute("SELECT id FROM tracks WHERE title=?", (title,)).fetchone()[0]


def test_changes_on_both_sides_meet(pair):
    server, desk = pair
    t = tid_of(desk, "Yesterday")
    likes.set_liked(desk, "owner", "track", str(t), True)                  # a like on the desktop
    server.set_moods([t], ["Грустное"], "set")                              # a label from a phone
    likes.set_liked(server, "u1", "album", "Кино\x1fГруппа крови", True)     # a listener's like
    pushed, pulled = exchange(server, desk)
    assert pushed["applied"] >= 1 and pulled["applied"] >= 2
    assert likes.is_liked(server, "owner", "track", str(t))
    assert desk.get_track(t)["moods"] == ["Грустное"]
    assert likes.is_liked(desk, "u1", "album", "Кино\x1fГруппа крови")
    assert exchange(server, desk) == ({"applied": 0, "skipped": 0, "new": 0, "tracks": []},
                                      {"applied": 0, "skipped": 0, "new": 0, "tracks": []})   # no echo


def test_removals_travel_too(pair):
    server, desk = pair
    t = tid_of(server, "Yesterday")
    server.set_genres([t], ["Rock"], "set") if hasattr(server, "set_genres") else None
    likes.set_liked(server, "owner", "track", str(t), True)
    exchange(server, desk)
    assert likes.is_liked(desk, "owner", "track", str(t))
    likes.set_liked(desk, "owner", "track", str(t), False)
    exchange(server, desk)
    assert not likes.is_liked(server, "owner", "track", str(t))


def test_the_later_edit_of_the_same_field_wins(pair):
    server, desk = pair
    t = tid_of(server, "Yesterday")
    server.conn.execute("UPDATE tracks SET lang='en' WHERE id=?", (t,))
    time.sleep(0.05)
    desk.conn.execute("UPDATE tracks SET lang='ru' WHERE id=?", (t,))      # later
    desk.conn.execute("UPDATE tracks SET vocal='male' WHERE id=?", (t,))    # another field: no conflict
    exchange(server, desk)
    for db in (server, desk):
        row = db.conn.execute("SELECT lang, vocal FROM tracks WHERE id=?", (t,)).fetchone()
        assert (row[0], row[1]) == ("ru", "male")


def test_new_tracks_come_from_the_server_with_the_desktop_path(pair, library, tmp_path):
    server, desk = pair
    make_mp3(library / "Новое" / "Песня.mp3", artist="Новое feat. Гость", title="Песня")
    scan(server, library, tmp_path / "covers")
    mirror = tmp_path / "mirror"

    def map_path(p):
        return str(mirror / "Новое" / "Песня.mp3")

    _, pulled = exchange(server, desk, map_path)
    assert pulled["new"] == 1
    t = tid_of(server, "Песня")
    row = desk.get_track(t)
    missing = desk.conn.execute("SELECT missing FROM tracks WHERE id=?", (t,)).fetchone()[0]
    assert row["path"] == str(mirror / "Новое" / "Песня.mp3") and missing == 0          # the server has it
    assert desk.conn.execute("SELECT rowid FROM fts WHERE fts MATCH 'Песня'").fetchone()[0] == t   # the search index
    assert server.get_track(t)["path"] != row["path"]                                        # paths stay local
    assert desk.search("Песня")[0]["id"] == t
    server.mark_missing([server.get_track(t)["path"]])                                       # gone on the server
    exchange(server, desk)
    assert not desk.search("Песня")


def test_desktop_made_tracks_are_not_taken_by_the_server(pair, tmp_path):
    server, desk = pair
    make_mp3(tmp_path / "local" / "A" / "x.mp3", artist="A", title="Только тут")
    scan(desk, tmp_path / "local", tmp_path / "c2")
    exchange(server, desk)
    assert not server.conn.execute("SELECT 1 FROM tracks WHERE title='Только тут'").fetchone()


def test_renamed_mood_keeps_its_labels_and_plays_and_playlists_travel(pair):
    server, desk = pair
    t = tid_of(desk, "Yesterday")
    desk.set_moods([t], ["Грустное"], "set")
    exchange(server, desk)
    desk.rename_mood("Грустное", "Печальное")
    desk.record_play(t, 120.0)
    pid = desk.create_playlist("Вечер")
    desk.playlist_add(pid, [t, tid_of(desk, "Группа крови")])
    exchange(server, desk)
    assert server.get_track(t)["moods"] == ["Печальное"]
    assert server.conn.execute("SELECT COUNT(*) FROM plays WHERE track_id=?", (t,)).fetchone()[0] == 1
    spid = server.conn.execute("SELECT id FROM playlists WHERE name='Вечер'").fetchone()[0]
    assert [r[0] for r in server.conn.execute(
        "SELECT track_id FROM playlist_tracks WHERE playlist_id=? ORDER BY pos", (spid,))] == \
        [t, tid_of(desk, "Группа крови")]


def test_a_catalogue_without_sync_journals_nothing(tmp_path, library):
    db = Database(tmp_path / "plain.db")
    scan(db, library, tmp_path / "covers")
    assert not sync.enabled(db.conn)
    assert not db.conn.execute("SELECT 1 FROM sqlite_master WHERE name='sync_log'").fetchone()
    db.close()


def test_enabling_survives_reopening(tmp_path, library):
    db = Database(tmp_path / "x.db")
    n = sync.enable(db.conn)
    db.close()
    db = Database(tmp_path / "x.db")
    assert sync.enabled(db.conn) and sync.node(db.conn) == n
    likes.set_liked(db, "owner", "artist", "Кино", True)
    assert sync.head(db.conn) == 1
    db.close()
