"""Moods and tags from the internet, for the mood model and as suggestions.

Sources (all free, no account needed except Last.fm):
* MusicBrainz (1 request/second, mandatory User-Agent): finds the recording by artist + title and the artist's tags;
* AcousticBrainz: ready-made high-level predictions per recording (mood_happy / sad / aggressive / relaxed / party /
  acoustic / electronic) computed by Essentia models.  Coverage of non-English music is partial;
* Last.fm (optional, needs a free API key in MUSICPLAYER_LASTFM_KEY): crowd tags such as "sad", "chill", "melancholy" -
  the best source of real mood words.

Everything is cached in the catalogue (``web_cache``), so a track is asked for once.  Network access goes through a
small ``Http`` object so tests can replace it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np

from .db import Database
from .feat import parse as parse_feat
from .jobs import JobContext, null_context

log = logging.getLogger(__name__)
UA = "MusicPlayer/0.1 (personal music library tool)"
MB_INTERVAL = 1.1

# AcousticBrainz class -> the app's mood
AB_MOODS = {"mood_sad": "Грустное", "mood_happy": "Весёлое", "mood_aggressive": "Злое",
            "mood_relaxed": "Спокойное", "mood_party": "Энергичное"}
AB_FEATURES = ("mood_sad", "mood_happy", "mood_aggressive", "mood_relaxed", "mood_party", "mood_acoustic", "mood_electronic")

# crowd-tag keywords -> mood (Last.fm style tags; lowercase substrings)
TAG_MOODS = {
    "Грустное": ("sad", "melanchol", "depress", "sorrow", "heartbreak", "gloomy", "lonely", "tearjerker"),
    "Весёлое": ("happy", "cheerful", "feel good", "feel-good", "fun", "upbeat", "sunshine", "joy", "uplifting"),
    "Энергичное": ("energetic", "energy", "party", "dance", "driving", "pump", "upbeat"),
    "Спокойное": ("calm", "chill", "relax", "mellow", "peaceful", "soothing", "easy listening", "lounge"),
    "Злое": ("aggressive", "angry", "rage", "brutal", "heavy", "intense"),
    "Меланхоличное": ("bittersweet", "wistful", "tender", "romantic"),
    "Ностальгия": ("nostalg", "retro", "oldies", "throwback"),
    "Фоновое": ("ambient", "background", "atmospheric", "soundscape", "drone"),
    "Ночное": ("night", "nocturnal", "sleep", "lullaby", "dreamy", "slumber"),
}


class Http:
    """Rate-limited JSON GET (MusicBrainz asks for at most one request per second)."""

    def __init__(self, interval: float = MB_INTERVAL) -> None:
        self.interval = interval
        self._last = 0.0

    def get_json(self, url: str, retries: int = 3) -> dict | None:
        for attempt in range(retries):
            wait = self._last + self.interval - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=40) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                if e.code in (429, 503):
                    time.sleep(5 * (attempt + 1))
                    continue
                log.warning("HTTP %s for %s", e.code, url)
                return None
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                time.sleep(2 * (attempt + 1))
        return None


def _ensure(db: Database) -> None:
    db.conn.execute("CREATE TABLE IF NOT EXISTS web_cache(key TEXT PRIMARY KEY, data TEXT, at REAL)")
    db.conn.execute("CREATE TABLE IF NOT EXISTS track_web(track_id INTEGER PRIMARY KEY, mbid TEXT, ab TEXT, tags TEXT, at REAL)")


def _cached(db: Database, key: str):
    r = db.conn.execute("SELECT data FROM web_cache WHERE key=?", (key,)).fetchone()
    return (True, json.loads(r[0])) if r else (False, None)


def _store(db: Database, key: str, value) -> None:
    db.conn.execute("INSERT OR REPLACE INTO web_cache VALUES(?,?,?)", (key, json.dumps(value, ensure_ascii=False), time.time()))


def mb_candidates(db: Database, http: Http, artist: str, title: str) -> list[str]:
    """MusicBrainz recording ids matching artist + title (best first, score >= 85)."""
    key = f"mbsearch:{artist.casefold()}|{title.casefold()}"
    hit, val = _cached(db, key)
    if hit:
        return val
    q = urllib.parse.urlencode({"query": f'recording:"{title}" AND artist:"{artist}"', "fmt": "json", "limit": 4})
    d = http.get_json("https://musicbrainz.org/ws/2/recording/?" + q) or {}
    ids = [r["id"] for r in d.get("recordings", []) if r.get("score", 0) >= 85]
    _store(db, key, ids)
    return ids


def artist_tags(db: Database, http: Http, artist: str) -> list[list]:
    """[[tag, count], ...] of the artist from MusicBrainz (top 15), cached per artist."""
    key = f"mbartist:{artist.casefold()}"
    hit, val = _cached(db, key)
    if hit:
        return val
    tags: list[list] = []
    q = urllib.parse.urlencode({"query": f'artist:"{artist}"', "fmt": "json", "limit": 1})
    d = http.get_json("https://musicbrainz.org/ws/2/artist/?" + q) or {}
    arts = d.get("artists") or []
    if arts and arts[0].get("score", 0) >= 90:
        full = http.get_json(f"https://musicbrainz.org/ws/2/artist/{arts[0]['id']}?inc=tags+genres&fmt=json") or {}
        raw = full.get("tags", []) + full.get("genres", [])
        merged: dict[str, int] = {}
        for t in raw:
            merged[t["name"].lower()] = max(merged.get(t["name"].lower(), 0), t.get("count", 0))
        tags = [[k, v] for k, v in sorted(merged.items(), key=lambda kv: -kv[1])[:15]]
    _store(db, key, tags)
    return tags


def is_negative_class(name: str) -> bool:
    """AcousticBrainz class names: 'Not sad', 'not_sad', 'non_electronic' ... are the negative class."""
    n = name.strip().lower().replace("_", " ")
    return n.startswith("not ") or n.startswith("non ") or n.startswith("non-")


def acousticbrainz(db: Database, http: Http, mbids: list[str]) -> dict[str, dict]:
    """{mbid: {mood_x: probability_of_positive_class}} for recordings AcousticBrainz knows (cached, 25 per request)."""
    out: dict[str, dict] = {}
    todo = []
    for m in mbids:
        hit, val = _cached(db, f"ab:{m}")
        if hit:
            if val:
                out[m] = val
        else:
            todo.append(m)
    for i in range(0, len(todo), 25):
        chunk = todo[i:i + 25]
        url = "https://acousticbrainz.org/api/v1/high-level?map_classes=true&recording_ids=" + ";".join(chunk)
        d = http.get_json(url) or {}
        for m in chunk:
            moods: dict = {}
            entry = d.get(m) or d.get(m.lower())
            if isinstance(entry, dict):
                sub = next((v for v in entry.values() if isinstance(v, dict) and "highlevel" in v), None)
                hl = (sub or {}).get("highlevel", {})
                for name in AB_FEATURES:
                    cls = (hl.get(name) or {}).get("all")
                    if cls:
                        pos = next((p for k, p in cls.items() if not is_negative_class(k)), None)
                        if pos is not None:
                            moods[name] = float(pos)
            _store(db, f"ab:{m}", moods)
            if moods:
                out[m] = moods
    return out


def lastfm_tags(db: Database, http: Http, artist: str, title: str, key: str) -> list[list]:
    """Crowd tags [[tag, weight], ...] of a track from Last.fm (needs an API key)."""
    ck = f"lastfm:{artist.casefold()}|{title.casefold()}"
    hit, val = _cached(db, ck)
    if hit:
        return val
    q = urllib.parse.urlencode({"method": "track.gettoptags", "artist": artist, "track": title, "api_key": key,
                                "format": "json", "autocorrect": 1})
    d = http.get_json("https://ws.audioscrobbler.com/2.0/?" + q) or {}
    tags = [[t["name"].lower(), int(t.get("count", 0))] for t in (d.get("toptags") or {}).get("tag", [])][:20]
    _store(db, ck, tags)
    return tags


def tags_to_moods(tags: list[list]) -> dict[str, float]:
    """Mood scores (0..1) from crowd tags: weight share of tags whose text contains a mood keyword."""
    if not tags:
        return {}
    total = sum(max(w, 1) for _, w in tags) or 1
    out: dict[str, float] = {}
    for mood, words in TAG_MOODS.items():
        s = sum(max(w, 1) for t, w in tags if any(k in t for k in words))
        if s:
            out[mood] = min(1.0, 3 * s / total)
    return out


def fetch_tracks(db: Database, ids: list[int] | None = None, ctx: JobContext | None = None, http: Http | None = None,
                 lastfm_key: str | None = None) -> dict:
    """Fill ``track_web`` (MBID, AcousticBrainz moods, artist tags) for tracks that have no row yet."""
    _ensure(db)
    ctx = ctx or null_context()
    http = http or Http()
    lastfm_key = lastfm_key or os.environ.get("MUSICPLAYER_LASTFM_KEY")
    have = {r[0] for r in db.conn.execute("SELECT track_id FROM track_web")}
    rows = [r for r in db.conn.execute("SELECT id,artist,title FROM tracks WHERE missing=0").fetchall()
            if r["id"] not in have and (ids is None or r["id"] in set(ids))]
    if ids is not None:                      # process in the order the caller gave (e.g. the labelling sample)
        order = {t: i for i, t in enumerate(ids)}
        rows.sort(key=lambda r: order[r["id"]])
    ctx.log(f"Интернет-теги: {len(rows)} треков (MusicBrainz ~1 запрос/с, кэш сохраняется)")
    ctx.progress(0, len(rows), "start")
    found_ab = found_tags = 0
    for i, r in enumerate(rows, 1):
        ctx.check()
        title = parse_feat(r["title"], r["artist"]).title
        artist = (r["artist"] or "").strip()
        mbids = mb_candidates(db, http, artist, title) if artist and title else []
        ab = acousticbrainz(db, http, mbids) if mbids else {}
        chosen = next((m for m in mbids if m in ab), mbids[0] if mbids else None)
        tags = artist_tags(db, http, artist) if artist else []
        if lastfm_key and artist and title:
            tags = [list(t) for t in {tuple(x) for x in tags + lastfm_tags(db, http, artist, title, lastfm_key)}]
        db.conn.execute("INSERT OR REPLACE INTO track_web VALUES(?,?,?,?,?)",
                        (r["id"], chosen, json.dumps(ab.get(chosen, {}) if chosen else {}), json.dumps(tags, ensure_ascii=False),
                         time.time()))
        if i % 20 == 0:
            db.conn.commit()                 # an interrupted run keeps what it fetched
        found_ab += bool(chosen and ab.get(chosen))
        found_tags += bool(tags)
        ctx.progress(i, len(rows), f"{i}/{len(rows)}: AcousticBrainz {found_ab}, теги {found_tags}")
    ctx.log(f"Готово: настроения AcousticBrainz найдены для {found_ab} из {len(rows)}, теги исполнителя для {found_tags}")
    return {"fetched": len(rows), "with_acousticbrainz": found_ab, "with_tags": found_tags}


def get_web(db: Database, tid: int) -> dict | None:
    _ensure(db)
    r = db.conn.execute("SELECT mbid,ab,tags FROM track_web WHERE track_id=?", (tid,)).fetchone()
    if not r:
        return None
    return {"mbid": r[0], "ab": json.loads(r[1] or "{}"), "tags": json.loads(r[2] or "[]")}


def web_moods(web: dict, min_prob: float = 0.0) -> dict[str, float]:
    """Mood scores of a track from its web data: AcousticBrainz probabilities, else crowd-tag keywords."""
    out = {AB_MOODS[k]: p for k, p in web.get("ab", {}).items() if k in AB_MOODS and p >= min_prob}
    for m, s in tags_to_moods(web.get("tags", [])).items():
        out.setdefault(m, s * 0.5)             # tag keywords are weaker evidence than a trained classifier
    return out


def suggest_from_web(db: Database, ids: list[int] | None = None, min_conf: float = 0.7, ab_only: bool = True) -> dict:
    """Store web mood estimates as suggestions (source 'web'): strong AcousticBrainz classes only by default."""
    _ensure(db)
    rows = db.conn.execute("SELECT track_id FROM track_web").fetchall()
    items = []
    for (tid,) in rows:
        if ids is not None and tid not in ids:
            continue
        web = get_web(db, tid)
        scores = {AB_MOODS[k]: p for k, p in web["ab"].items() if k in AB_MOODS} if ab_only else web_moods(web)
        items += [(tid, m, p) for m, p in scores.items() if p >= min_conf]
    n = db.suggest_moods(items, "web")
    for t in {i[0] for i in items}:
        db.refresh_fts(t)
    return {"suggested": n, "tracks": len({i[0] for i in items})}


def agreement(db: Database) -> dict:
    """How well the web estimates match the moods YOU confirmed (precision of strong web guesses, per mood)."""
    _ensure(db)
    truth = dict(db.mood_training_set())
    hits = tot = covered = 0
    per: dict[str, list[int]] = {}
    for tid, ms in truth.items():
        web = get_web(db, tid)
        if not web:
            continue
        sc = {AB_MOODS[k]: p for k, p in web["ab"].items() if k in AB_MOODS}
        if not sc:
            continue
        covered += 1
        for m, p in sc.items():
            if p >= 0.7 and m in db_moods(db):
                tot += 1
                ok = m in ms
                hits += ok
                d = per.setdefault(m, [0, 0])
                d[1] += 1
                d[0] += ok
    return {"labelled_with_web_moods": covered, "strong_guesses": tot, "correct": hits,
            "precision": round(hits / tot, 3) if tot else None,
            "per_mood": {m: {"guesses": b, "precision": round(a / b, 2)} for m, (a, b) in per.items()}}


def db_moods(db: Database) -> set[str]:
    return {m["name"] for m in db.list_moods()}


# ---- features for the mood model --------------------------------------------------------------
def top_tags(db: Database, k: int = 30) -> list[str]:
    """The k most common artist tags in the library (the vocabulary of the tag features)."""
    _ensure(db)
    cnt: dict[str, int] = {}
    for (t,) in db.conn.execute("SELECT tags FROM track_web"):
        for name, _ in json.loads(t or "[]"):
            cnt[name] = cnt.get(name, 0) + 1
    return [n for n, _ in sorted(cnt.items(), key=lambda kv: -kv[1])[:k]]


def web_vector(web: dict | None, vocab: list[str]) -> np.ndarray:
    """[7 AcousticBrainz probabilities (NaN when unknown)] + [tag weight per vocabulary tag] + [has_data]."""
    ab = np.array([(web or {}).get("ab", {}).get(k, np.nan) for k in AB_FEATURES], dtype=float)
    tags = {n: c for n, c in (web or {}).get("tags", [])}
    top = max(tags.values(), default=0) or 1
    tv = np.array([tags.get(t, 0) / top for t in vocab], dtype=float)
    return np.concatenate([ab, tv, [1.0 if web and (web.get("tags") or web.get("ab")) else 0.0]])
