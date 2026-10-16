"""Titles broken by a downloader: every Cyrillic letter became U+FFFD («�») in the tags (and the file name).

The letters are gone from the files, but much survives: the album and the artist folder, the track number, the
spaces, Latin letters, digits and punctuation - «������ ��� ��������» is three words of 6, 3 and 8 letters.  That mask
is matched against the release's tracklist on MusicBrainz:

* a candidate fits when it has the same length, every «�» stands for a non-ASCII character and every other character
  is the same (ASCII letters case-insensitively);
* several fitting titles of one length are told apart by the track number;
* a tail the release does not have - «(feat. …)», «(cover)», «(live)» - is matched separately: the base title against
  the tracklist, «feat.» names against the track's artist credit; a tail that cannot be read is dropped;
* a title the release does not have is looked up among all recordings of the artist (accepted only if exactly one
  title fits the mask).

Corrections go into the catalogue (``Database.edit_track``: they survive rescans); files are not touched.
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

from .db import Database

BAD = "�"
FIELDS = ("title", "artist", "album", "albumartist")


def is_broken(s: str | None) -> bool:
    return bool(s) and BAD in s


def fits(broken: str, cand: str) -> bool:
    if len(broken) != len(cand):
        return False
    return all((b == BAD and ord(c) > 127) or b.casefold() == c.casefold() for b, c in zip(broken, cand))


def find_broken(db: Database) -> list[dict]:
    q = " OR ".join(f"instr({k}, char(65533))" for k in FIELDS)
    rows = db.conn.execute(f"SELECT id, path, track_no, {','.join(FIELDS)} FROM tracks WHERE missing=0 AND ({q})")
    return [dict(r) for r in rows]


def _track_no(t: dict) -> int | None:
    if t.get("track_no"):
        return int(t["track_no"])
    m = re.match(r"\s*(\d{1,3})\b", Path(t["path"]).name)
    return int(m.group(1)) if m else None


def _folder(t: dict) -> tuple[str, str]:
    """(artist, album) from the library layout ``<artist>/<album> (<year>)/file``."""
    p = Path(t["path"]).parent
    return p.parent.name, re.sub(r"\s*\(\d{4}\)$", "", p.name)


def tracklist(http, artist: str, album: str, limit: int = 3) -> list[dict]:
    """[{position, title, credit, names}] of the best-matching releases (several editions concatenated)."""
    q = urllib.parse.urlencode({"query": f'release:"{album}" AND artist:"{artist}"', "fmt": "json", "limit": 5})
    rels = (http.get_json("https://musicbrainz.org/ws/2/release/?" + q) or {}).get("releases", [])
    out = []
    for rel in rels[:limit]:
        full = http.get_json(f"https://musicbrainz.org/ws/2/release/{rel['id']}?inc=recordings+artist-credits"
                             "&fmt=json") or {}
        for m in full.get("media", []):
            for t in m.get("tracks", []):
                ac = t.get("artist-credit", [])
                out.append({"position": t.get("position"), "title": t["title"],
                            "credit": "".join(a["name"] + a.get("joinphrase", "") for a in ac),
                            "names": [a["name"] for a in ac]})
    return out


def _pick(broken: str, tracks: list[dict], no: int | None) -> dict | None:
    cands = [t for t in tracks if fits(broken, t["title"])]
    if len({t["title"] for t in cands}) > 1 and no is not None:
        cands = [t for t in cands if t["position"] == no]
    return cands[0] if len({t["title"] for t in cands}) == 1 else None


_TAIL = re.compile(r"^(.*?)(\s*[(\[].*[)\]])$")


def recover_title(broken: str, tracks: list[dict], no: int | None) -> tuple[str | None, dict | None]:
    """(recovered title or None, the matched track)."""
    t = _pick(broken, tracks, no)
    if t:
        return t["title"], t
    m = _TAIL.match(broken)
    if not m:
        return None, None
    base, tail = m.groups()
    t = _pick(base, tracks, no)
    if not t:
        return None, None
    inner = tail.strip()[1:-1]
    feat = re.match(r"(?i)(feat\.?|ft\.?|при уч\.?)\s+(.*)$", inner)
    if feat and BAD in feat.group(2):
        others = t["names"][1:]
        for joined in (", ".join(others), " & ".join(others), " и ".join(others)):
            if others and fits(feat.group(2), joined):
                return f"{t['title']}{tail.replace(feat.group(2), joined)}", t
    return (t["title"] + tail if BAD not in tail else t["title"]), t   # an unreadable tail is dropped


def artist_titles(http, artist: str) -> list[dict]:
    """Every recording title of the artist on MusicBrainz (up to 300), as tracklist-like dicts without a position."""
    out, seen = [], set()
    for offset in (0, 100, 200):
        q = urllib.parse.urlencode({"query": f'artist:"{artist}"', "fmt": "json", "limit": 100, "offset": offset})
        recs = (http.get_json("https://musicbrainz.org/ws/2/recording/?" + q) or {}).get("recordings", [])
        for r in recs:
            ac = r.get("artist-credit", [])
            if r["title"].casefold() not in seen:
                seen.add(r["title"].casefold())
                out.append({"position": None, "title": r["title"], "names": [a["name"] for a in ac],
                            "credit": "".join(a["name"] + a.get("joinphrase", "") for a in ac)})
        if len(recs) < 100:
            break
    return out


def recover_artist(broken: str, folder_artist: str, track: dict | None) -> str | None:
    for cand in ([track["credit"]] if track else []) + [folder_artist]:
        if fits(broken, cand):
            return cand
    m = re.match(r"^(.*?)(\s+(?:feat\.?|ft\.?)\s+)(.*)$", broken, re.I)
    if m and fits(m.group(1), folder_artist):
        rest = m.group(3)
        if track and len(track["names"]) > 1 and fits(rest, ", ".join(track["names"][1:])):
            rest = ", ".join(track["names"][1:])
        return folder_artist + m.group(2) + rest
    return None


def propose(db: Database, http, ctx=None) -> list[dict]:
    """[{id, path, field, old, new}] for every broken field that could be read back."""
    from collections import defaultdict
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for t in find_broken(db):
        groups[_folder(t)].append(t)
    out: list[dict] = []
    for n, ((artist, album_dir), ts) in enumerate(groups.items(), 1):
        if ctx is not None:
            ctx.check()
            ctx.progress(n, len(groups), f"{artist} — {album_dir}")
        album = next((t["album"] for t in ts if t["album"] and not is_broken(t["album"])), album_dir)
        tracks = tracklist(http, artist, album)
        everything: list[dict] | None = None
        for t in ts:
            match = None
            if is_broken(t["title"]):
                new, match = recover_title(t["title"], tracks, _track_no(t))
                if not new:
                    if everything is None:
                        everything = artist_titles(http, artist)
                    new, match = recover_title(t["title"], everything, None)
                if new and new != t["title"]:
                    out.append({"id": t["id"], "path": t["path"], "field": "title", "old": t["title"], "new": new})
            for f in ("artist", "albumartist"):
                if is_broken(t[f]):
                    new = recover_artist(t[f], artist, match)
                    if new:
                        out.append({"id": t["id"], "path": t["path"], "field": f, "old": t[f], "new": new})
            if is_broken(t["album"]) and fits(t["album"], album_dir):
                out.append({"id": t["id"], "path": t["path"], "field": "album", "old": t["album"], "new": album_dir})
    return out


def apply(db: Database, proposals: list[dict]) -> int:
    by: dict[int, dict] = {}
    for p in proposals:
        by.setdefault(p["id"], {})[p["field"]] = p["new"]
    for tid, fields in by.items():
        db.edit_track([tid], fields)
    return len(by)
