"""Recording-type flags: live, acoustic, demo, remix, cover.  Detected from title / album, edited by hand.

They matter for labelling (a live or demo recording of a studio song often has poor sound that confuses audio
analysis), for filtering ("no live, no demos") and for training (live / demo tracks are left out of the mood model's
training data)."""

from __future__ import annotations

import re

from .db import Database

FLAGS = {"live": "Live", "acoustic": "Acoustic", "demo": "Demo", "remix": "Remix", "cover": "Cover", "explicit": "Мат", "female": "Женский вокал", "male": "Мужской вокал", "raspy": "Хриплый вокал", "rarity": "Раритетная запись"}
POOR_SOUND = ("live", "demo")                     # excluded from mood training (unless the user labelled them)

_PATTERNS = {
    # "live" counts only as a recording marker (in brackets, after a dash, "live at/in/from", "... live" at the end,
    # or in the album name) - not as an ordinary word ("we'll live", "live up").
    "live": r"[(\[@\-–—][^)\]]*\blive\b|\blive (at|in|from|on|session|lounge|acoustic|version|recording)\b|"
            r"\b(acoustic|unplugged|radio) live\b|концерт|в живую|\bunplugged\b|\bbbc\b|radio ?1\b|\bsessions?\b|\barena\b",
    "acoustic": r"\bacoustic\b|\bакустик|\bакуст\.?\b|\bacustic\b|\bunplugged\b",
    "demo": r"\bdemo\b|демо|\bbootleg\b|\brehearsal\b|репетиц|\bunreleased\b",
    "remix": r"\bremix\b|ремикс|\bmix\)|\brework\b|\bedit\)",
    "cover": r"\bcover\b|кавер|\btribute\b|трибьют",
}
_COMPILED = {k: re.compile(v, re.I) for k, v in _PATTERNS.items()}
_ALBUM_LIVE = re.compile(r"\blive\b", re.I)


def detect(title: str | None, album: str | None = "", path: str | None = "") -> set[str]:
    """Flags found in the track's title and album (folders are too noisy to use)."""
    title, album = title or "", album or ""
    found = {k for k, rx in _COMPILED.items() if rx.search(title) or rx.search(album)}
    if _ALBUM_LIVE.search(album):                    # "Illusions Live - Viretta Park": the whole album is live
        found.add("live")
    return found


def apply_detected(db: Database) -> dict:
    """Write detected flags (source 'auto') for tracks whose flags the user never edited."""
    n = 0
    counts: dict[str, int] = {}
    for r in db.conn.execute("SELECT id,title,album,path FROM tracks WHERE missing=0 AND (flags_src IS NULL OR flags_src='auto')").fetchall():
        found = detect(r["title"], r["album"], r["path"])
        other = {x[0] for x in db.conn.execute("SELECT flag FROM track_flags WHERE track_id=?", (r["id"],))} - set(_PATTERNS)
        db.set_flags([r["id"]], sorted(found | other), mode="set", src="auto")   # keep flags not found in titles (vocals...)
        for f in found:
            counts[f] = counts.get(f, 0) + 1
        n += 1
    return {"tracks": n, "counts": counts}
