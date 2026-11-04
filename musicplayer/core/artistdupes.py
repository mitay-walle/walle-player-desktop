"""Duplicate artists: one band under several spellings («Story of the Year» / «Story Of The Year», «blink‐182» /
«blink-182», «Мёртвые дельфины» / «Мертвые Дельфины»).  Found by a spelling-blind key, merged into the name most tracks
use: the catalogue corrections of core/db.py (files are not touched, rescans keep them), likes of the artist move too.
Qt-free."""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

from .db import Database


def key(name: str) -> str:
    """The same for every spelling of one name: case, ё/е, «The», «&»/«and», punctuation and spaces do not count."""
    s = unicodedata.normalize("NFKC", name or "").casefold().replace("ё", "е")
    s = re.sub(r"^the\s+|,\s*the$", "", s.strip())
    s = re.sub(r"\s*(&|\+)\s*", " and ", s)
    return re.sub(r"[\W_]+", "", s)


def _looks_right(name: str) -> int:
    """Tie-break: a name in mixed case beats ALL CAPS and all lower case."""
    return int(name != name.upper()) + int(name != name.lower())


def find(db: Database) -> list[dict]:
    """Groups of spellings: [{"names": [{"name", "tracks"}...], "best": name}], the biggest groups first."""
    counts: dict[str, int] = defaultdict(int)
    for r in db.conn.execute("SELECT artist, albumartist FROM tracks WHERE missing=0"):
        for n in {r[0], r[1]} - {None, ""}:
            counts[n] += 1
    groups: dict[str, list[str]] = defaultdict(list)
    for n in counts:
        k = key(n)
        if k:
            groups[k].append(n)
    out = []
    for names in groups.values():
        if len(names) < 2:
            continue
        names.sort(key=lambda n: (-counts[n], -_looks_right(n), n))
        out.append({"names": [{"name": n, "tracks": counts[n]} for n in names], "best": names[0]})
    out.sort(key=lambda g: -sum(x["tracks"] for x in g["names"]))
    return out


def merge(db: Database, names: list[str], into: str) -> int:
    """Every track of ``names`` gets the artist (and album artist) ``into``; returns how many tracks changed."""
    other = [n for n in names if n != into]
    if not other or not into.strip():
        return 0
    q = ",".join("?" * len(other))
    changed = 0
    for tid, artist, albumartist in db.conn.execute(
            f"SELECT id, artist, albumartist FROM tracks WHERE artist IN ({q}) OR albumartist IN ({q})",
            other + other).fetchall():
        fields = {}
        if artist in other:
            fields["artist"] = into
        if albumartist in other:
            fields["albumartist"] = into
        changed += db.edit_track([tid], fields)
    from .likes import GUEST_TABLE
    db.conn.execute(GUEST_TABLE)
    for n in other:                                      # a liked spelling: the merged artist stays liked
        if db.conn.execute("SELECT 1 FROM favorites WHERE kind='artist' AND key=?", (n,)).fetchone():
            db.conn.execute("INSERT OR IGNORE INTO favorites SELECT kind, ?, added FROM favorites "
                            "WHERE kind='artist' AND key=?", (into, n))
            db.conn.execute("DELETE FROM favorites WHERE kind='artist' AND key=?", (n,))
        db.conn.execute("INSERT OR IGNORE INTO remote_favorites SELECT user, kind, ?, added FROM remote_favorites "
                        "WHERE kind='artist' AND key=?", (into, n))
        db.conn.execute("DELETE FROM remote_favorites WHERE kind='artist' AND key=?", (n,))
    db.conn.commit()
    return changed
