"""Copy / paste of all the labels of a track: moods, recording-type flags, language, vocals, genres, era.

Pasting replaces the target tracks' labels with the copied ones (moods become the user's own, source 'manual')."""

from __future__ import annotations

from .db import Database


def snapshot(db: Database, tid: int) -> dict | None:
    t = db.get_track(tid)
    if t is None:
        return None
    return {"moods": list(t["moods"]), "flags": list(t.get("flags", [])), "lang": t.get("lang"), "vocal": t.get("vocal"),
            "genres": list(t.get("genres", [])), "era": t.get("era"),
            "source": f"{t['artist']} — {t['title']}"}


def paste(db: Database, tids: list[int], snap: dict) -> None:
    db.set_moods(tids, snap["moods"], "set")
    db.set_flags(tids, snap["flags"], "set")
    db.set_language(tids, snap["lang"], src="manual")
    db.set_vocal(tids, snap["vocal"], src="manual")
    db.set_genres(tids, snap["genres"], "set")
    db.set_era(tids, snap["era"])
    db.conn.commit()


def describe(snap: dict | None) -> str:
    if not snap:
        return "Буфер меток пуст"
    parts = snap["moods"] + snap["genres"] + snap["flags"] + [x for x in (snap["lang"], snap["vocal"]) if x]
    return f"Метки трека «{snap['source']}»: " + (", ".join(parts) or "нет меток")
