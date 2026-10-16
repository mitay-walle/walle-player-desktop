"""«Пометить на удаление»: listeners mark a track or an album from their phone / the web player; the owner looks through
the list in the desktop app and deletes them (Recycle Bin) or takes the mark off.  Qt-free.

Keys follow «Избранное»: a track by its id, an album as "artist\\x1falbum" (the album artist, else the artist).
The list can be saved to a JSON file and loaded elsewhere (another computer, another copy of the library): there the
tracks are found by their path, else by artist and title (core/likeimport.py).
"""

from __future__ import annotations

import json
import time

KINDS = {"track": "Треки", "album": "Альбомы"}
SEP = "\x1f"
SCHEMA = ("CREATE TABLE IF NOT EXISTS delete_marks(kind TEXT, key TEXT, user TEXT, added REAL, "
          "PRIMARY KEY(kind, key, user))")


def ensure(db) -> None:
    db.conn.execute(SCHEMA)


def album_key(artist: str, album: str) -> str:
    return f"{artist or ''}{SEP}{album or ''}"


def track_album_key(t: dict) -> str:
    return album_key(t.get("albumartist") or t.get("artist") or "", t.get("album") or "")


def set_mark(db, user: str, kind: str, key: str, on: bool = True, added: float | None = None) -> None:
    if kind not in KINDS or not str(key).strip():
        raise ValueError("kind: track | album, key: required")
    ensure(db)
    if on:
        db.conn.execute("INSERT OR IGNORE INTO delete_marks VALUES(?,?,?,?)", (kind, str(key), user, added or time.time()))
    else:
        db.conn.execute("DELETE FROM delete_marks WHERE kind=? AND key=? AND user=?", (kind, str(key), user))
    db.conn.commit()


def clear(db, kind: str, key: str) -> None:
    """«Снять пометку»: whoever marked it."""
    ensure(db)
    db.conn.execute("DELETE FROM delete_marks WHERE kind=? AND key=?", (kind, str(key)))
    db.conn.commit()


def marked_by(db, user: str) -> list[dict]:
    """What this listener marked: [{kind, key}] (a client shows «Снять пометку» for them)."""
    ensure(db)
    return [{"kind": k, "key": key} for k, key in db.conn.execute(
        "SELECT kind, key FROM delete_marks WHERE user=? ORDER BY added DESC", (user,))]


def album_tracks(db, key: str) -> list[dict]:
    artist, _, album = key.partition(SEP)
    rows = db.search(artist=artist, album=album, order="album", limit=10**6) if album else []
    return [r for r in rows if (r.get("albumartist") or r.get("artist") or "").casefold() == artist.casefold()]


def listing(db, names: dict[str, str] | None = None) -> list[dict]:
    """Everything marked, newest first: {kind, key, title, sub, by: [names], added, tracks: [track dicts]}.
    Marks of tracks / albums that are gone from the library are dropped."""
    ensure(db)
    names = names or {}
    grouped: dict[tuple[str, str], dict] = {}
    for kind, key, user, added in db.conn.execute("SELECT kind, key, user, added FROM delete_marks ORDER BY added DESC"):
        g = grouped.setdefault((kind, key), {"kind": kind, "key": key, "by": [], "added": added})
        g["by"].append(names.get(user, "вы" if user == "owner" else user))
        g["added"] = max(g["added"], added)
    out = []
    for (kind, key), g in grouped.items():
        if kind == "track":
            t = db.get_track(int(key)) if key.isdigit() else None
            if t is None or db.conn.execute("SELECT missing FROM tracks WHERE id=?", (t["id"],)).fetchone()[0]:
                continue
            g.update(title=t["title"] or "без названия", sub=f"{t['artist']} — {t['album'] or 'без альбома'}", tracks=[t])
        else:
            tracks = album_tracks(db, key)
            if not tracks:
                continue
            artist, _, album = key.partition(SEP)
            g.update(title=album or "без альбома", sub=f"{artist} · треков: {len(tracks)}", tracks=tracks)
        out.append(g)
    return sorted(out, key=lambda g: g["added"], reverse=True)


def count(db) -> int:
    ensure(db)
    return db.conn.execute("SELECT COUNT(*) FROM (SELECT DISTINCT kind, key FROM delete_marks)").fetchone()[0]


def drop_deleted(db, track_ids) -> None:
    """Deleted tracks take their marks along (and an album mark once none of its tracks is left)."""
    ensure(db)
    db.conn.executemany("DELETE FROM delete_marks WHERE kind='track' AND key=?", [(str(i),) for i in track_ids])
    for (key,) in db.conn.execute("SELECT DISTINCT key FROM delete_marks WHERE kind='album'").fetchall():
        if not album_tracks(db, key):
            db.conn.execute("DELETE FROM delete_marks WHERE kind='album' AND key=?", (key,))
    db.conn.commit()


# ---- a file to take the list elsewhere ---------------------------------------------------------------------
def export(db, names: dict[str, str] | None = None) -> str:
    """JSON: every marked track (an album as its tracks too) with path, artist, title, album, who and when."""
    items = []
    for g in listing(db, names):
        for t in g["tracks"]:
            items.append({"kind": g["kind"], "path": t["path"], "artist": t["artist"], "title": t["title"],
                          "album": t["album"] or "", "albumartist": t.get("albumartist") or "",
                          "marked_by": g["by"], "added": g["added"]})
    return json.dumps({"app": "Walle Player", "what": "delete_marks", "exported": time.time(), "items": items},
                      ensure_ascii=False, indent=1)


def load(db, text: str, user: str = "owner") -> dict:
    """Marks from an exported file: a track by its path, else by artist and title; an album mark is restored as an
    album when all its tracks were marked as an album.  {"marked": n, "unmatched": [«artist — title»]}."""
    from . import likeimport
    data = json.loads(text)
    items = data.get("items", []) if isinstance(data, dict) else data
    by_path = {r[1].casefold(): r[0] for r in db.conn.execute("SELECT id, path FROM tracks WHERE missing=0")}
    found, rest = [], []
    for it in items:
        tid = by_path.get(str(it.get("path", "")).casefold())
        (found.append((it, tid)) if tid else rest.append(it))
    res = likeimport.match(db, [{"artist": it.get("artist", ""), "title": it.get("title", ""), "src": it} for it in rest])
    found += [(i["src"], t["id"]) for i, t in res["matched"]]
    albums = set()
    for it, tid in found:
        if it.get("kind") == "album":
            t = db.get_track(tid)
            albums.add(track_album_key(t))
        else:
            set_mark(db, user, "track", str(tid), True, it.get("added"))
    for key in albums:
        set_mark(db, user, "album", key, True)
    return {"marked": len(found), "unmatched": [likeimport.describe(i) for i in res["unmatched"]]}
