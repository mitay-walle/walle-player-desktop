"""Import likes from a file into a listener: plain text, CSV/TSV, JSON or an M3U playlist.  Qt-free.

Formats (whatever a streaming service or a playlist-transfer tool exports):

* text - one track per line: «Artist - Title» (any dash: - – —), numbering like «12. » is ignored;
* CSV / TSV - a header row with an artist column (artist, artists, artist name, исполнитель ...) and a title column
  (title, track, track name, name, song, название ...);
* JSON - a list of «Artist - Title» strings or of objects with artist/title (``artists: [{name}]`` and a wrapping
  ``{"tracks": [...]}`` / ``{"items": [...]}`` as Yandex Music / Spotify exports have them);
* M3U / M3U8 - ``#EXTINF:…,Artist - Title`` lines, or file paths (then the track is found by its path or file name).

Matching ignores case, «ё», punctuation, «feat. …» and bracketed extras («(Remastered 2011)», «[Live]»); a small typo
is forgiven (similarity >= 0.88 on the title and >= 0.6 on the artist).  The owner's likes are the app's «Избранное»,
a guest's go to ``remote_favorites`` (see server/api.py).
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
from difflib import SequenceMatcher, get_close_matches
from pathlib import Path

_DASH = re.compile(r"\s+[-–—]+\s+|\s*[–—]\s*")
_NUM = re.compile(r"^\s*\d{1,4}[.)]\s+")
_FEAT = re.compile(r"\s*[(\[]?\s*\b(feat|ft|featuring|при участии)\b\.?.*$", re.I)
_BRACKETS = re.compile(r"[(\[{][^)\]}]*[)\]}]")
_NONWORD = re.compile(r"[^\w]+")

ARTIST_COLS = ("artist", "artists", "artist name", "artist name(s)", "artist_name", "исполнитель", "исполнители",
               "performer", "band")
DATE_COLS = ("added at", "added_at", "date added", "added", "liked at", "date", "timestamp", "дата добавления", "дата")
TITLE_COLS = ("title", "track", "track name", "track_name", "name", "song", "song name", "название", "трек")


def norm(text: str | None) -> str:
    """Comparable form: lower case, ё -> е, without «feat. …», bracketed extras and punctuation."""
    s = (text or "").casefold().replace("ё", "е")
    s = _BRACKETS.sub(" ", _FEAT.sub("", s))
    return " ".join(_NONWORD.sub(" ", s).split())


def _split_line(line: str) -> dict | None:
    line = _NUM.sub("", line.strip().lstrip("﻿"))
    if not line or line.startswith("#"):
        return None
    parts = _DASH.split(line, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return {"artist": parts[0].strip(), "title": parts[1].strip()}
    return {"artist": "", "title": line}


def parse_time(v) -> float | None:
    """When the track was liked, from an export: unix seconds / milliseconds, ISO 8601, «2024-05-01 12:30», «01.05.2024»."""
    from datetime import datetime, timezone
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and re.fullmatch(r"\d{9,13}(\.\d+)?", v.strip())):
        x = float(v)
        return x / 1000 if x > 1e11 else x
    s = str(v).strip().replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            d = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
        except ValueError:
            continue
        return (d if d.tzinfo else d.astimezone()).astimezone(timezone.utc).timestamp()
    return None


def _from_obj(o) -> dict | None:
    if isinstance(o, str):
        return _split_line(o)
    if not isinstance(o, dict):
        return None
    added = parse_time(next((o[k] for k in ("added_at", "addedAt", "added", "timestamp", "liked_at", "date") if o.get(k)), None))
    item = _from_track(o.get("track") if isinstance(o.get("track"), dict) else o)   # {"track": {...}} (Spotify, Yandex)
    if item is not None and added:
        item["added"] = added
    return item


def _from_track(o: dict) -> dict | None:
    title = o.get("title") or o.get("name") or o.get("track") or o.get("song") or ""
    artist = o.get("artist") or o.get("artists") or o.get("performer") or ""
    if isinstance(artist, list):
        artist = ", ".join(a.get("name", "") if isinstance(a, dict) else str(a) for a in artist)
    elif isinstance(artist, dict):
        artist = artist.get("name", "")
    if not title and o.get("path"):
        return {"artist": "", "title": "", "path": str(o["path"])}
    return {"artist": str(artist).strip(), "title": str(title).strip()} if title else None


def parse(text: str, filename: str = "") -> list[dict]:
    """[{artist, title}] or [{path}] items from the file's text; the format comes from the name and the content."""
    ext = Path(filename).suffix.lower()
    stripped = text.lstrip("﻿ \r\n\t")
    if ext == ".json" or stripped[:1] in "[{":
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if data is not None:
            if isinstance(data, dict):
                data = next((data[k] for k in ("tracks", "items", "likes", "favorites", "songs") if isinstance(data.get(k), list)),
                            [data])
            return [i for i in (_from_obj(o) for o in data) if i]
    if ext in (".m3u", ".m3u8") or stripped.startswith("#EXTM3U"):
        out, pending = [], None
        for line in text.splitlines():
            line = line.strip()
            if line.upper().startswith("#EXTINF"):
                pending = _split_line(line.split(",", 1)[1]) if "," in line else None
            elif line and not line.startswith("#"):
                out.append({**(pending or {"artist": "", "title": ""}), "path": line})
                pending = None
        return out
    first = stripped.splitlines()[0] if stripped else ""
    if ext in (".csv", ".tsv") or (("," in first or "\t" in first or ";" in first)
                                   and any(c in first.casefold() for c in ARTIST_COLS + TITLE_COLS)):
        dialect = "excel-tab" if "\t" in first else "excel"
        delim = ";" if first.count(";") > first.count(",") and "\t" not in first else None
        reader = csv.DictReader(io.StringIO(stripped), dialect=dialect, **({"delimiter": delim} if delim else {}))
        cols = {(c or "").strip().casefold(): c for c in (reader.fieldnames or [])}
        a = next((cols[c] for c in ARTIST_COLS if c in cols), None)
        t = next((cols[c] for c in TITLE_COLS if c in cols), None)
        d = next((cols[c] for c in DATE_COLS if c in cols), None)
        if t:
            out = []
            for r in reader:
                if not (r.get(t) or "").strip():
                    continue
                item = {"artist": (r.get(a) or "").strip() if a else "", "title": (r.get(t) or "").strip()}
                when = parse_time(r.get(d)) if d else None
                if when:
                    item["added"] = when
                out.append(item)
            return out
    return [i for i in (_split_line(line) for line in text.splitlines()) if i]


def _sim(a: str, b: str) -> float:
    return 1.0 if a == b else SequenceMatcher(None, a, b).ratio()


def match(db, items: list[dict]) -> dict:
    """{"matched": [(item, track)], "unmatched": [item]}; each library track is matched at most once."""
    rows = db.conn.execute("SELECT id, title, artist, albumartist, path FROM tracks WHERE missing=0").fetchall()
    by_title: dict[str, list] = {}
    by_path: dict[str, dict] = {}
    for r in rows:
        t = {"id": r[0], "title": r[1] or "", "artist": r[2] or "", "albumartist": r[3] or "", "path": r[4] or ""}
        t["nt"] = norm(t["title"])
        t["na"] = {norm(x) for x in (t["artist"], t["albumartist"]) if x} | {norm(p) for p in re.split(r",|&| x |/", t["artist"]) if p.strip()}
        by_title.setdefault(t["nt"], []).append(t)
        by_path[t["path"].casefold()] = t
        by_path.setdefault(Path(t["path"]).name.casefold(), t)
    titles = [tt for tt in by_title if tt]
    used: set[int] = set()
    matched, unmatched = [], []

    def artist_score(want: str, t: dict) -> float:
        if not want:
            return 0.7                                    # no artist in the file: the title decides
        parts = {norm(p) for p in re.split(r",|&| x |/| и | and ", want, flags=re.I) if p.strip()} | {norm(want)}
        return max((_sim(p, a) for p in parts for a in t["na"] if p and a), default=0.0)

    for item in items:
        best = None
        if item.get("path"):
            p = item["path"].casefold()
            best = by_path.get(p) or by_path.get(Path(p.replace("\\", "/")).name)
        if best is None and item.get("title"):
            nt = norm(item["title"])
            cands = [(artist_score(item.get("artist", ""), t), 1.0, t) for t in by_title.get(nt, [])]
            if not cands or max(c[0] for c in cands) < 0.6:          # a typo in the title: the closest titles
                cands = [(artist_score(item.get("artist", ""), t), _sim(nt, tt), t)
                         for tt in get_close_matches(nt, titles, n=5, cutoff=0.88) for t in by_title[tt]]
            cands = [c for c in cands if c[0] >= 0.6 and c[2]["id"] not in used]
            if cands:
                if not item.get("artist") and len({c[2]["id"] for c in cands}) > 1:
                    cands = []                           # a bare title shared by several songs: do not guess
                else:
                    best = max(cands, key=lambda c: (c[1], c[0]))[2]
        if best is not None and best["id"] not in used:
            used.add(best["id"])
            matched.append((item, best))
        else:
            unmatched.append(item)
    return {"matched": matched, "unmatched": unmatched}


def apply(db, user: dict, track_ids: list[int], added: list[float | None] | None = None) -> int:
    """Likes the tracks for the listener (owner -> «Избранное», guest -> remote_favorites); returns how many are new.
    ``added``: when each was liked (from the export); without a date the file's order is kept (first line = newest)."""
    now = time.time()
    added = added or [None] * len(track_ids)
    rows = [(str(tid), when or now - k * 1e-3) for k, (tid, when) in enumerate(zip(track_ids, added))]
    before = db.conn.total_changes
    dated = {str(tid) for tid, when in zip(track_ids, added) if when}
    # a like that exists already keeps its time - unless the file says when it was liked (a re-import fixes times)
    if user.get("owner"):
        db.conn.executemany("INSERT OR IGNORE INTO favorites VALUES('track',?,?)", rows)
        db.conn.executemany("UPDATE favorites SET added=? WHERE kind='track' AND key=? AND added<>?",
                            [(t, k, t) for k, t in rows if k in dated])
    else:
        db.conn.execute("CREATE TABLE IF NOT EXISTS remote_favorites(user TEXT, kind TEXT, key TEXT, added REAL, "
                        "PRIMARY KEY(user, kind, key))")
        db.conn.executemany("INSERT OR IGNORE INTO remote_favorites VALUES(?,'track',?,?)",
                            [(user["id"], k, t) for k, t in rows])
        db.conn.executemany("UPDATE remote_favorites SET added=? WHERE user=? AND kind='track' AND key=? AND added<>?",
                            [(t, user["id"], k, t) for k, t in rows if k in dated])
    db.conn.commit()
    return db.conn.total_changes - before


def describe(item: dict) -> str:
    if item.get("artist") or item.get("title"):
        return f"{item.get('artist') or '?'} — {item.get('title') or '?'}"
    return item.get("path", "?")


def read_file(path: str | Path) -> str:
    raw = Path(path).read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "cp1251"):          # Windows text editors and old exports save in cp1251
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")
