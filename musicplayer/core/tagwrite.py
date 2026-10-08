"""Writing genre tags into the audio files - a separate, explicit, reversible operation.

The database stays the source of truth for edits; nothing touches a file until
:func:`write_genres` is called.  Before a file is changed its previous genre tag is saved in
``tag_backups`` so :func:`restore_genres` can put it back.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable

import mutagen
from mutagen.easyid3 import EasyID3

from .db import Database, split_genres


def _ensure_table(db: Database) -> None:
    db.conn.execute("CREATE TABLE IF NOT EXISTS tag_backups(id INTEGER PRIMARY KEY, track_id INTEGER, path TEXT, "
                    "genres_before TEXT, genres_after TEXT, written_at REAL, restored INTEGER DEFAULT 0)")


def _read_genres(path: str) -> list[str]:
    audio = mutagen.File(path, easy=True)
    return split_genres(list(audio.tags.get("genre", []))) if audio is not None and audio.tags else []


def pending(db: Database, ids: Iterable[int] | None = None) -> list[dict]:
    """Tracks whose DB genres differ from the file tag as scanned."""
    ch = db.genre_changes()
    if ids is not None:
        want = set(ids)
        ch = [c for c in ch if c["id"] in want]
    return ch


def write_genres(db: Database, ids: Iterable[int] | None = None, dry_run: bool = True) -> dict:
    """Write DB genres to files.  ``dry_run`` (default) only reports what would change."""
    _ensure_table(db)
    todo = pending(db, ids)
    if dry_run:
        return {"dry_run": True, "would_change": len(todo), "changes": todo[:200]}
    done, errors = [], []
    for c in todo:
        try:
            path = Path(c["path"])
            before = _read_genres(c["path"])
            audio = mutagen.File(path, easy=True)
            if audio is None:
                raise ValueError("unsupported file")
            if audio.tags is None:
                audio.add_tags()
            audio["genre"] = c["db"]
            if isinstance(audio, mutagen.mp3.MP3):
                audio.save(v2_version=3)
            else:
                audio.save()
            st = path.stat()
            db.conn.execute("INSERT INTO tag_backups(track_id,path,genres_before,genres_after,written_at) "
                            "VALUES(?,?,?,?,?)", (c["id"], c["path"], "; ".join(before), "; ".join(c["db"]), time.time()))
            db.conn.execute("UPDATE tracks SET genres_file=?, mtime=?, size=? WHERE id=?",
                            ("; ".join(c["db"]), st.st_mtime, st.st_size, c["id"]))
            done.append(c["id"])
        except Exception as e:
            errors.append({"id": c["id"], "path": c["path"], "error": str(e)})
    return {"dry_run": False, "written": len(done), "ids": done, "errors": errors}


def restore_genres(db: Database, ids: Iterable[int] | None = None) -> dict:
    """Put back the genre tags that :func:`write_genres` replaced (latest backup per track)."""
    _ensure_table(db)
    q = "SELECT id,track_id,path,genres_before FROM tag_backups WHERE restored=0 ORDER BY id DESC"
    seen, restored, errors = set(), [], []
    want = set(ids) if ids is not None else None
    for r in db.conn.execute(q).fetchall():
        tid = r["track_id"]
        if tid in seen or (want is not None and tid not in want):
            continue
        seen.add(tid)
        try:
            audio = mutagen.File(r["path"], easy=True)
            before = split_genres(r["genres_before"])
            if before:
                audio["genre"] = before
            elif audio.tags and "genre" in audio.tags:
                del audio.tags["genre"]
            audio.save(v2_version=3) if isinstance(audio, mutagen.mp3.MP3) else audio.save()
            st = Path(r["path"]).stat()
            db.conn.execute("UPDATE tracks SET genres_file=?, mtime=?, size=? WHERE id=?",
                            (r["genres_before"], st.st_mtime, st.st_size, tid))
            db.conn.execute("UPDATE tag_backups SET restored=1 WHERE track_id=?", (tid,))
            restored.append(tid)
        except Exception as e:
            errors.append({"id": tid, "error": str(e)})
    return {"restored": len(restored), "ids": restored, "errors": errors}
