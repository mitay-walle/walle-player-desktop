"""Male / female vocals from MusicBrainz artist data (flags 'male' / 'female', source 'auto').

* A solo artist (type Person) has a gender.
* A group lists its members; members credited with (lead) vocals have a gender too.  Current vocalists win over former
  ones; with no vocal credits the gender of the group's members is not used (a band of men can have a female singer).

Only the catalogue changes; tracks the user edited by hand and instrumental tracks are left alone."""

from __future__ import annotations

import urllib.parse

from .db import Database
from .jobs import JobContext
from .webtags import Http, _cached, _ensure, _store

MB = "https://musicbrainz.org/ws/2"


def artist_id(db: Database, http: Http, name: str) -> str | None:
    _ensure(db)
    key = f"mbartistid:{name.casefold()}"
    hit, val = _cached(db, key)
    if hit:
        return val
    q = urllib.parse.urlencode({"query": f'artist:"{name}"', "fmt": "json", "limit": 1})
    d = http.get_json(f"{MB}/artist/?" + q) or {}
    arts = d.get("artists") or []
    mbid = arts[0]["id"] if arts and arts[0].get("score", 0) >= 90 else None
    _store(db, key, mbid)
    return mbid


def artist_info(db: Database, http: Http, mbid: str) -> dict:
    """{type, gender, vocalists: [{id, ended}]} (cached)."""
    key = f"mbartistinfo2:{mbid}"
    hit, val = _cached(db, key)
    if hit:
        return val
    d = http.get_json(f"{MB}/artist/{mbid}?inc=artist-rels&fmt=json") or {}
    vocal = []
    for rel in d.get("relations", []):
        if rel.get("type") != "member of band" or rel.get("direction") != "backward":
            continue
        attrs = " ".join(rel.get("attributes", [])).lower()
        if "vocal" in attrs:
            vocal.append({"id": rel["artist"]["id"], "ended": bool(rel.get("ended")), "lead": "lead" in attrs,
                          "begin": rel.get("begin"), "end": rel.get("end")})
    info = {"type": d.get("type"), "gender": (d.get("gender") or "").lower() or None, "vocalists": vocal}
    _store(db, key, info)
    return info


def tenure(v: dict, now: int = 2026) -> int:
    """Years a member was in the band (unknown dates: a current member counts 5 years, a former one 3)."""
    def year(x):
        try:
            return int(str(x)[:4])
        except (TypeError, ValueError):
            return None
    b, e = year(v.get("begin")), year(v.get("end"))
    if b is None:
        return 3 if v.get("ended") else 5
    return max((e or (b if v.get("ended") else now)) - b, 0)


def vocal_genders(db: Database, http: Http, name: str) -> set[str]:
    """{'male'} / {'female'} / both / empty for an artist name."""
    mbid = artist_id(db, http, name)
    if not mbid:
        return set()
    info = artist_info(db, http, mbid)
    if info["type"] in ("Person", "Character"):
        return {info["gender"]} & {"male", "female"}
    vs = info["vocalists"]
    lead = [v for v in vs if v["lead"]] or vs
    # the vocalist who sang longest in the band (a library mostly holds the classic line-up, not a 2024 newcomer)
    years = {id(v): tenure(v) for v in lead}
    best = max(years.values(), default=0)
    lead = [v for v in lead if years[id(v)] >= best - 2]
    out: set[str] = set()
    for v in lead[:4]:
        g = artist_info(db, http, v["id"]).get("gender")
        if g in ("male", "female"):
            out.add(g)
    return out


def tag_library(db: Database, ctx: JobContext | None = None, http: Http | None = None) -> dict:
    ctx = ctx or JobContext()
    http = http or Http()
    _ensure(db)
    rows = db.conn.execute("SELECT id, COALESCE(NULLIF(albumartist,''), artist) AS a, vocal FROM tracks "
                           "WHERE missing=0 AND (flags_src IS NULL OR flags_src='auto')").fetchall()
    by_artist: dict[str, list] = {}
    for r in rows:
        if r["a"]:
            by_artist.setdefault(r["a"], []).append(r)
    counts = {"male": 0, "female": 0}
    found = 0
    for i, (artist, trs) in enumerate(sorted(by_artist.items()), 1):
        ctx.check()
        genders = vocal_genders(db, http, artist)
        db.conn.commit()
        if genders:
            found += 1
            ids = [t["id"] for t in trs if t["vocal"] != "inst"]
            db.set_flags(ids, sorted(genders), mode="add", src="auto")
            db.conn.commit()
            for g in genders:
                counts[g] += len(ids)
        ctx.progress(i, len(by_artist), f"{artist}: {', '.join(sorted(genders)) or '—'}")
    return {"artists": len(by_artist), "artists_found": found, "tracks": counts}
