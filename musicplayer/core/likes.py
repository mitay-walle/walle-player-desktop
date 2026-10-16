"""Likes of one listener, the same place the library server keeps them.  Qt-free.

The owner's likes are the app's «Избранное» (table favorites); a guest's live in remote_favorites (see server/api.py).
Keys: a track by its id, an album as "artist\\x1falbum", an artist by name.  ``added`` (unix time) orders them.
"""

from __future__ import annotations

import time

OWNER = "owner"
GUEST_TABLE = ("CREATE TABLE IF NOT EXISTS remote_favorites(user TEXT, kind TEXT, key TEXT, added REAL, "
               "PRIMARY KEY(user, kind, key))")


def is_owner(user: str | None) -> bool:
    return not user or user == OWNER


def rows(db, user: str, kind: str) -> list[tuple[str, float]]:
    """(key, added) newest first."""
    if is_owner(user):
        return [(r[0], r[1] or 0.0) for r in db.conn.execute(
            "SELECT key, added FROM favorites WHERE kind=? ORDER BY added DESC", (kind,))]
    db.conn.execute(GUEST_TABLE)
    return [(r[0], r[1] or 0.0) for r in db.conn.execute(
        "SELECT key, added FROM remote_favorites WHERE user=? AND kind=? ORDER BY added DESC", (user, kind))]


def keys(db, user: str, kind: str) -> list[str]:
    return [k for k, _ in rows(db, user, kind)]


def is_liked(db, user: str, kind: str, key: str) -> bool:
    if is_owner(user):
        return db.is_favorite(kind, key)
    db.conn.execute(GUEST_TABLE)
    return db.conn.execute("SELECT 1 FROM remote_favorites WHERE user=? AND kind=? AND key=?",
                           (user, kind, key)).fetchone() is not None


def set_liked(db, user: str, kind: str, key: str, on: bool = True, added: float | None = None) -> None:
    if is_owner(user):
        if on:
            db.conn.execute("INSERT OR IGNORE INTO favorites VALUES(?,?,?)", (kind, key, added or time.time()))
        else:
            db.conn.execute("DELETE FROM favorites WHERE kind=? AND key=?", (kind, key))
    else:
        db.conn.execute(GUEST_TABLE)
        if on:
            db.conn.execute("INSERT OR IGNORE INTO remote_favorites VALUES(?,?,?,?)", (user, kind, key, added or time.time()))
        else:
            db.conn.execute("DELETE FROM remote_favorites WHERE user=? AND kind=? AND key=?", (user, kind, key))
    db.conn.commit()
