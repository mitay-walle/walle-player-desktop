"""'My wave': endless listening by mood, and every like/dislike teaches the mood model.

Candidates are tracks you CONFIRMED for the chosen moods ("known") plus tracks the model/Claude only GUESSED
for them with enough confidence ("guess").  A share of every batch (``explore``) is guesses: while you listen
you rate them - 👍 confirms the mood (a new label), 👎 rejects it (a negative example).  Character:
favourite (played more), unfamiliar (rarely played, never a liked track), mixed, weak (the least certain guesses first: rating them
fixes the labels the model is worst at).  Genres, lyrics languages and voices narrow the wave (any of the chosen
ones of each); a wave of those only, without moods, plays every track that fits.  Radio: a wave from one track or
one artist - the closest tracks by sound and tags (core/similar.py), the moods are not asked then.  Qt-free.
"""

from __future__ import annotations

import math
import random
import time

from .db import Database, rare_weights

CHARACTERS = {"favorite": "Любимое", "unfamiliar": "Незнакомое", "mixed": "Вперемешку", "weak": "Плохо размеченное"}
VOICES = {"voc": "С вокалом", "inst": "Без слов", "female": "Женский вокал", "male": "Мужской вокал",
          "raspy": "Хриплый вокал"}
RECENT_DAYS = 3.0                                       # «not again so soon»: a track heard d days ago weighs 1-e^(-d/3)
RADIO_TOP = 300                                         # a radio plays among the closest tracks only
RADIO_TTL = 600.0
_radio_cache: dict[tuple, tuple[float, dict[int, float]]] = {}


def radio_scores(db: Database, track: int | None = None, artist: str | None = None) -> dict[int, float]:
    """{track id: closeness} of the RADIO_TOP closest tracks to a track / an artist (the artist's own tracks first).
    Kept for 10 minutes: every next batch of the same radio asks again."""
    key = (db.path, track, (artist or "").casefold())
    hit = _radio_cache.get(key)
    if hit and time.monotonic() - hit[0] < RADIO_TTL:
        return hit[1]
    from .similar import radio_scores as closeness
    pool = db.search(limit=10**9)
    if track is not None:
        seeds = [t for t in pool if t["id"] == track] or ([db.get_track(track)] if db.get_track(track) else [])
    else:
        a = (artist or "").casefold()
        seeds = [t for t in pool if a in ((t["artist"] or "").casefold(), (t.get("albumartist") or "").casefold())]
    if not seeds:
        return {}
    got = closeness(db, seeds, pool)
    top = dict(sorted(got.items(), key=lambda x: x[1], reverse=True)[:RADIO_TOP])
    if artist is not None:                               # the artist's own songs belong to his radio
        top.update({s["id"]: 1.0 for s in seeds})
    else:
        top.pop(track, None)
    _radio_cache[key] = (time.monotonic(), top)
    if len(_radio_cache) > 32:
        _radio_cache.pop(next(iter(_radio_cache)))
    return top


def _tracks(db: Database, ids: list[int]) -> list[dict]:
    from .db import TRACK_COLS
    rows = []
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        rows += [dict(r) for r in db.conn.execute(
            f"SELECT {TRACK_COLS} FROM tracks WHERE missing=0 AND id IN ({','.join('?' * len(part))})", part)]
    return db._enrich(rows)


def wave_filters(db: Database) -> dict:
    """What the wave can be narrowed to in this library: {"wave_langs": [{key, name, n}], "wave_voices": [...]}."""
    from .langvocal import LANGS
    langs = [{"key": r[0], "name": LANGS.get(r[0], r[0]).capitalize(), "n": r[1]} for r in db.conn.execute(
        "SELECT lang, COUNT(*) AS n FROM tracks WHERE missing=0 AND lang IS NOT NULL AND lang<>'' GROUP BY lang "
        "ORDER BY n DESC")]
    if any(x["key"] == "ru" for x in langs) and len(langs) > 1:
        langs.insert(1, {"key": "foreign", "name": "Не на русском",
                         "n": sum(x["n"] for x in langs if x["key"] != "ru")})
    n = dict(db.conn.execute("SELECT vocal, COUNT(*) FROM tracks WHERE missing=0 AND vocal IS NOT NULL GROUP BY vocal"))
    n.update(db.conn.execute("SELECT f.flag, COUNT(*) FROM track_flags f JOIN tracks t ON t.id=f.track_id "
                             "WHERE t.missing=0 AND f.flag IN ('female','male','raspy') GROUP BY f.flag"))
    voices = [{"key": k, "name": v, "n": n.get(k, 0)} for k, v in VOICES.items() if n.get(k)]
    return {"wave_langs": langs, "wave_voices": voices}


def voice_fits(t: dict, voices: set[str]) -> bool:
    flags = set(t.get("flags") or [])
    have = set(flags & {"female", "male", "raspy"})
    if t.get("vocal") == "inst":
        have.add("inst")
    elif t.get("vocal") == "voc" or have:
        have.add("voc")
    return bool(have & voices)


def lang_fits(t: dict, langs: set[str]) -> bool:
    lang = t.get("lang")
    return lang in langs or ("foreign" in langs and lang != "ru")


def recency(last_played: float | None, now: float) -> float:
    """How welcome a track is again: 0.02 just heard, ~0.3 a day later, ~0.6 after three days, ~0.9 after a week;
    never heard - 1. (Only a 2-hour pause before: what was heard yesterday came back as often as anything else.)"""
    if not last_played:
        return 1.0
    days = max(now - last_played, 0.0) / 86400
    return max(0.02, 1.0 - math.exp(-days / RECENT_DAYS))


class Wave:
    def __init__(self, db: Database, moods: list[str] | None = None, character: str = "mixed",
                 min_conf: float = 0.55, explore: float = 0.3, rng: random.Random | None = None,
                 genres: list[str] | None = None, plays: dict[int, tuple[int, float | None]] | None = None,
                 user: str | None = "owner", langs: list[str] | None = None, voices: list[str] | None = None,
                 seed_track: int | None = None, seed_artist: str | None = None,
                 heard: dict[int, float] | None = None) -> None:
        """``plays``: {track id: (count, last played)} of another listener (a guest on a phone): favourite/unfamiliar
        and «not again so soon» then follow HIS listening, not the catalogue's.  ``langs``: lyrics languages
        (core/langvocal.py LANGS, "foreign" = not Russian); ``voices``: VOICES keys.  ``seed_track`` /
        ``seed_artist``: a radio from that track / artist instead of moods.  ``heard``: {track id: when it started
        playing} on the listener's device - skipped ones too (a play counts only after half the track), so a wave
        started anew does not bring back what was just skipped."""
        self.db = db
        self.plays = plays
        self.user = user                                 # whose «Не показывать» the wave respects (None: nobody's)
        self.moods = [m for m in (moods or [])]
        from .genres import canonical                   # «рок» / «rock» from an older client means «Rock»
        self.genres = [c for g in (genres or []) if g for c in (canonical(g) or [g])]
        self.character = character if character in CHARACTERS else "mixed"
        self.min_conf = min_conf
        self.explore = explore
        self.rng = rng or random.Random()
        self.langs = [str(x) for x in (langs or []) if x]
        self.voices = [str(x) for x in (voices or []) if x in VOICES]
        self.seed_track = seed_track
        self.seed_artist = seed_artist or None
        self.heard = heard or {}

    @property
    def is_radio(self) -> bool:
        return self.seed_track is not None or self.seed_artist is not None

    def _pool(self) -> tuple[dict[int, dict], dict[int, dict]]:
        """(known, guess) tracks keyed by id; each gets ``wave_kind`` and ``wave_moods`` (which chosen moods match)."""
        known: dict[int, dict] = {}
        guess: dict[int, dict] = {}
        if self.is_radio:                                # radio: the closest tracks, nothing to rate
            near = radio_scores(self.db, self.seed_track, self.seed_artist)
            for t in _tracks(self.db, list(near)):
                known[t["id"]] = {**t, "wave_kind": "known", "wave_moods": [], "wave_sim": near[t["id"]]}
        elif not self.moods and (self.genres or self.langs or self.voices):   # no moods: every track that fits
            rows = self.db.search(genre=self.genres[0], limit=10**9) if len(self.genres) == 1 else self.db.search(limit=10**9)
            known = {t["id"]: {**t, "wave_kind": "known", "wave_moods": []} for t in rows}
        by_mood = not self.is_radio and (self.moods or not (self.genres or self.langs or self.voices))
        names = (self.moods or [m["name"] for m in self.db.list_moods() if m["n"]]) if by_mood else []
        for mood in names:
            for t in self.db.search(mood=mood, mood_status="confirmed", limit=10**9):
                k = known.setdefault(t["id"], {**t, "wave_kind": "known", "wave_moods": []})
                k["wave_moods"].append(mood)
            for t in self.db.search(mood=mood, mood_status="suggested", limit=10**9):
                conf = max((s["confidence"] or 0 for s in t["mood_suggestions"] if s["mood"].casefold() == mood.casefold()),
                           default=0)
                if conf >= self.min_conf and t["id"] not in known and t["id"] not in guess:
                    g = guess.setdefault(t["id"], {**t, "wave_kind": "guess", "wave_moods": [], "wave_conf": conf})
                    g["wave_moods"].append(mood)
        # moods the model assigned by itself (source 'auto') are shown to the user as guesses to confirm
        for tid in [i for i, t in known.items()
                    if t["wave_moods"] and all(t["mood_sources"].get(m) == "auto" for m in t["wave_moods"])]:
            t = known.pop(tid)
            t["wave_kind"] = "auto"
            t["wave_conf"] = max((t["mood_conf"].get(m) or 0) for m in t["wave_moods"])
            guess[tid] = t
        if self.genres:
            want = {g.casefold() for g in self.genres}
            fits = lambda t: any(g.casefold() in want for g in t.get("genres", []))  # noqa: E731
            known = {i: t for i, t in known.items() if fits(t)}
            guess = {i: t for i, t in guess.items() if fits(t)}
        if self.langs:
            want_l = set(self.langs)
            known = {i: t for i, t in known.items() if lang_fits(t, want_l)}
            guess = {i: t for i, t in guess.items() if lang_fits(t, want_l)}
        if self.voices:
            want_v = set(self.voices)
            known = {i: t for i, t in known.items() if voice_fits(t, want_v)}
            guess = {i: t for i, t in guess.items() if voice_fits(t, want_v)}
        if self.user:                                   # «Не показывать»: never in this listener's wave
            from .hidden import matcher
            hidden = matcher(self.db, self.user)
            known = {i: t for i, t in known.items() if not hidden(t)}
            guess = {i: t for i, t in guess.items() if not hidden(t)}
        if self.character == "unfamiliar":              # a liked track, album or artist is not unfamiliar, however rarely played
            liked = self._liked()
            known = {i: t for i, t in known.items() if not liked(t)}
            guess = {i: t for i, t in guess.items() if not liked(t)}
        return known, guess

    def _liked(self):
        """``liked(track_dict) -> bool``: the listener liked the track, its album or one of its artists («A & B»)."""
        from .feat import split_collab
        from .likes import keys
        who = self.user or "owner"
        tracks = {int(k) for k in keys(self.db, who, "track") if str(k).isdigit()}
        artists = {k.casefold() for k in keys(self.db, who, "artist")}
        albums = {k.casefold() for k in keys(self.db, who, "album")}

        def liked(t: dict) -> bool:
            tags = {(t.get("artist") or "").casefold(), (t.get("albumartist") or "").casefold()} - {""}
            names = tags | {p.casefold() for a in tags for p in split_collab(a)}
            album = (t.get("album") or "").casefold()
            return (t["id"] in tracks or bool(names & artists)
                    or bool(album) and any(f"{a}\x1f{album}" in albums for a in tags))
        return liked

    def _weights(self, rows: list[dict]) -> list[float]:
        if self.plays is not None:
            rows = [{**r, "play_count": self.plays.get(r["id"], (0, None))[0],
                     "last_played": self.plays.get(r["id"], (0, None))[1]} for r in rows]
        plays = [r.get("play_count") or 0 for r in rows]
        if self.character == "favorite":
            w = [(1 + p) ** 0.5 for p in plays]
        elif self.character == "weak":
            w = [max(1.05 - (r.get("wave_conf") if r.get("wave_conf") is not None else 1.0), 0.03) ** 2 for r in rows]
        elif self.character == "unfamiliar":
            w = rare_weights(plays)
        else:
            w = [1.0] * len(rows)
        if self.is_radio:                                # the closer, the more often
            w = [x * max(r.get("wave_sim") or 0.0, 0.02) ** 2 for x, r in zip(w, rows)]
        now = time.time()
        return [x * recency(max(r.get("last_played") or 0, self.heard.get(r["id"], 0)) or None, now)
                for x, r in zip(w, rows)]

    def _sample(self, pool: dict[int, dict], n: int) -> list[dict]:
        rows = list(pool.values())
        if not rows or n <= 0:
            return []
        keyed = sorted(((self.rng.random() ** (1.0 / max(w, 1e-9)), r) for w, r in zip(self._weights(rows), rows)),
                       key=lambda x: x[0], reverse=True)
        return [r for _, r in keyed[:n]]

    def next_batch(self, n: int = 10, exclude: set[int] | None = None) -> list[dict]:
        exclude = exclude or set()
        known, guess = self._pool()
        known = {i: t for i, t in known.items() if i not in exclude}
        guess = {i: t for i, t in guess.items() if i not in exclude}
        if self.character == "weak" and guess:               # only the doubtful guesses, the least certain first
            n_guess = min(len(guess), n)
        else:
            n_guess = min(len(guess), round(n * self.explore)) if known else min(len(guess), n)
        batch = self._sample(guess, n_guess) + self._sample(known, n - n_guess)
        if len(batch) < n:                                   # not enough known: fill with more guesses
            batch += self._sample({i: t for i, t in guess.items() if i not in {b["id"] for b in batch}}, n - len(batch))
        self.rng.shuffle(batch)
        return batch

    def like(self, t: dict) -> dict:
        """👍: confirms the guessed moods of a guess (a new label); on a known track nothing changes."""
        if t.get("wave_kind") in ("guess", "auto") and t.get("wave_moods"):
            self.db.resolve_suggestions([t["id"]], accept=True, moods=t["wave_moods"])
            t["wave_kind"] = "known"
            return {"result": "confirmed", "moods": t["wave_moods"]}
        return {"result": "liked", "moods": []}

    def dislike(self, t: dict) -> dict:
        """👎: rejects the guess (negative example for the model); a known track is just skipped."""
        if t.get("wave_kind") in ("guess", "auto") and t.get("wave_moods"):
            self.db.resolve_suggestions([t["id"]], accept=False, moods=t["wave_moods"])
            return {"result": "rejected", "moods": t["wave_moods"]}
        return {"result": "skipped", "moods": []}
