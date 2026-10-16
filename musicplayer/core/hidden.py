"""«Не показывать»: tracks, albums, artists, genres and labels (moods) a listener does not want to see.  Qt-free.

Hidden things are per listener (``user``: "owner" for the desktop app and the owner's devices, a guest's id from
remote.json otherwise) and never deleted from the catalogue: they do not play in «Моя волна» and do not show in the
lists and the search of that listener; the «Скрытое» screen lists them with «Вернуть» (an accidental click is undone
there).  Keys follow «Избранное»: a track by its id, an album as "artist\\x1falbum", an artist / genre / mood by name.
"""

from __future__ import annotations

import time

KINDS = {"track": "Треки", "album": "Альбомы", "artist": "Исполнители", "genre": "Жанры", "mood": "Метки"}
SEP = "\x1f"
OWNER = "owner"
SCHEMA = ("CREATE TABLE IF NOT EXISTS hidden(user TEXT, kind TEXT, key TEXT COLLATE UNI, added REAL, "
          "PRIMARY KEY(user, kind, key))")

# a track is hidden for the user when it, its album, its artist (or album artist), one of its genres or one of its
# confirmed labels is hidden; COLLATE UNI compares like the app does (case and Cyrillic insensitive)
SQL_VISIBLE = f"""NOT EXISTS(SELECT 1 FROM hidden h WHERE h.user=? AND (
  (h.kind='track' AND h.key=CAST(t.id AS TEXT))
  OR (h.kind='artist' AND (h.key=t.artist COLLATE UNI OR h.key=COALESCE(t.albumartist,'') COLLATE UNI))
  OR (h.kind='album' AND (h.key=t.artist||char(31)||COALESCE(t.album,'') COLLATE UNI
                          OR h.key=COALESCE(t.albumartist,'')||char(31)||COALESCE(t.album,'') COLLATE UNI))
  OR (h.kind='genre' AND EXISTS(SELECT 1 FROM track_genres g WHERE g.track_id=t.id AND g.genre=h.key COLLATE UNI))
  OR (h.kind='mood' AND EXISTS(SELECT 1 FROM track_moods m JOIN moods mm ON mm.id=m.mood_id
                               WHERE m.track_id=t.id AND m.source IN ('manual','seed','auto') AND mm.name=h.key))))"""


def ensure(db) -> None:
    db.conn.execute(SCHEMA)


def album_key(artist: str, album: str) -> str:
    return f"{artist or ''}{SEP}{album or ''}"


def set_hidden(db, user: str, kind: str, key: str, on: bool = True) -> None:
    if kind not in KINDS or not str(key).strip():
        raise ValueError(f"unknown kind {kind!r} or empty key")
    if kind == "genre":                                  # «хип-хоп» / «hip hop» mean the catalogue's «Hip-Hop»
        from .genres import canonical
        key = (canonical(str(key)) or [str(key)])[0]
    ensure(db)
    if on:
        db.conn.execute("INSERT OR IGNORE INTO hidden VALUES(?,?,?,?)", (user, kind, str(key), time.time()))
    else:
        db.conn.execute("DELETE FROM hidden WHERE user=? AND kind=? AND key=?", (user, kind, str(key)))
    db.conn.commit()


def keys(db, user: str) -> dict[str, set[str]]:
    ensure(db)
    out: dict[str, set[str]] = {k: set() for k in KINDS}
    for kind, key in db.conn.execute("SELECT kind, key FROM hidden WHERE user=?", (user,)):
        if kind in out:
            out[kind].add(key.casefold() if kind != "track" else key)
    return out


def matcher(db, user: str):
    """``hidden(track_dict) -> bool`` for tracks already loaded (the wave's pool, a playlist ...)."""
    h = keys(db, user)
    if not any(h.values()):
        return lambda t: False

    def hidden(t: dict) -> bool:
        artists = {(t.get("artist") or "").casefold(), (t.get("albumartist") or "").casefold()} - {""}
        album = (t.get("album") or "").casefold()
        return (str(t["id"]) in h["track"] or bool(artists & h["artist"])
                or any(f"{a}{SEP}{album}" in h["album"] for a in artists)
                or any(g.casefold() in h["genre"] for g in t.get("genres", []))
                or any(m.casefold() in h["mood"] for m in t.get("moods", [])))
    return hidden


def visible_values(db, user: str, kind: str, rows: list[dict]) -> list[dict]:
    """Rows of db.list_values(kind) without the hidden ones: albums (or their artist), artists, genres."""
    h = keys(db, user)
    if kind == "artist":
        return [r for r in rows if (r["name"] or "").casefold() not in h["artist"]]
    if kind == "album":
        return [r for r in rows if (r.get("artist") or "").casefold() not in h["artist"]
                and album_key(r.get("artist") or "", r["name"]).casefold() not in h["album"]]
    if kind == "genre":
        return [r for r in rows if (r["name"] or "").casefold() not in h["genre"]]
    return rows


def listing(db, user: str) -> list[dict]:
    """Everything the user hid, newest first: {kind, key, title, sub, added} (a track gets its title and artist)."""
    ensure(db)
    out = []
    for kind, key, added in db.conn.execute("SELECT kind, key, added FROM hidden WHERE user=? ORDER BY added DESC", (user,)):
        title, sub = key, KINDS.get(kind, kind)
        if kind == "track":
            r = db.conn.execute("SELECT title, artist FROM tracks WHERE id=?", (int(key) if key.isdigit() else -1,)).fetchone()
            title, sub = (r[0] or "без названия", r[1] or "") if r else ("(трек удалён из библиотеки)", "")
        elif kind == "album":
            artist, _, album = key.partition(SEP)
            title, sub = album or "без альбома", artist
        out.append({"kind": kind, "key": key, "title": title, "sub": sub, "added": added})
    return out


def counts(db, user: str) -> dict[str, int]:
    ensure(db)
    return dict(db.conn.execute("SELECT kind, COUNT(*) FROM hidden WHERE user=? GROUP BY kind", (user,)).fetchall())
