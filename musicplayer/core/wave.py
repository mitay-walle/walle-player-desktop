"""'My wave': endless listening by mood, and every like/dislike teaches the mood model.

Candidates are tracks you CONFIRMED for the chosen moods ("known") plus tracks the model/Claude only GUESSED
for them with enough confidence ("guess").  A share of every batch (``explore``) is guesses: while you listen
you rate them - 👍 confirms the mood (a new label), 👎 rejects it (a negative example).  Character:
favourite (played more), unfamiliar (rarely played), mixed, weak (the least certain guesses first: rating them
fixes the labels the model is worst at).  Genres narrow the wave (any of the chosen genres); a wave of genres
only, without moods, plays every track of those genres.  Qt-free.
"""

from __future__ import annotations

import random
import time

from .db import Database, rare_weights

CHARACTERS = {"favorite": "Любимое", "unfamiliar": "Незнакомое", "mixed": "Вперемешку", "weak": "Плохо размеченное"}
RECENT_SEC = 2 * 3600


class Wave:
    def __init__(self, db: Database, moods: list[str] | None = None, character: str = "mixed",
                 min_conf: float = 0.55, explore: float = 0.3, rng: random.Random | None = None,
                 genres: list[str] | None = None, plays: dict[int, tuple[int, float | None]] | None = None,
                 user: str | None = "owner") -> None:
        """``plays``: {track id: (count, last played)} of another listener (a guest on a phone): favourite/unfamiliar
        and «not again so soon» then follow HIS listening, not the catalogue's."""
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

    def _pool(self) -> tuple[dict[int, dict], dict[int, dict]]:
        """(known, guess) tracks keyed by id; each gets ``wave_kind`` and ``wave_moods`` (which chosen moods match)."""
        known: dict[int, dict] = {}
        guess: dict[int, dict] = {}
        if self.genres and not self.moods:              # genres only: every track of them, nothing to rate
            for g in self.genres:
                for t in self.db.search(genre=g, limit=10**9):
                    known.setdefault(t["id"], {**t, "wave_kind": "known", "wave_moods": []})
            return known, guess
        names = self.moods or [m["name"] for m in self.db.list_moods() if m["n"]]
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
                    if all(t["mood_sources"].get(m) == "auto" for m in t["wave_moods"])]:
            t = known.pop(tid)
            t["wave_kind"] = "auto"
            t["wave_conf"] = max((t["mood_conf"].get(m) or 0) for m in t["wave_moods"])
            guess[tid] = t
        if self.genres:
            want = {g.casefold() for g in self.genres}
            fits = lambda t: any(g.casefold() in want for g in t.get("genres", []))  # noqa: E731
            known = {i: t for i, t in known.items() if fits(t)}
            guess = {i: t for i, t in guess.items() if fits(t)}
        if self.user:                                   # «Не показывать»: never in this listener's wave
            from .hidden import matcher
            hidden = matcher(self.db, self.user)
            known = {i: t for i, t in known.items() if not hidden(t)}
            guess = {i: t for i, t in guess.items() if not hidden(t)}
        return known, guess

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
        now = time.time()
        return [x * (0.05 if r.get("last_played") and now - r["last_played"] < RECENT_SEC else 1.0)
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
