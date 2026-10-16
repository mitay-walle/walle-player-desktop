"""Neighbour priors for the mood model: tracks of the same GENRE and of the same ARTIST tend to share moods.

For a track, the genre prior is the average mood vector of the user-labelled tracks that share its genres, the artist
prior the average over the artist's other labelled tracks (both shrunk towards the library average when there are few
of them).  The model's probability is blended with them:

    p = (1 - wg - wa) * p_audio + wg * genre_prior + wa * artist_prior

The weights are fitted honestly at training time:
* wg on artist-grouped out-of-fold predictions with the genre prior built WITHOUT the track's artist (a new artist);
* wa on random-split out-of-fold predictions with both priors built without the track itself (a known artist).
A track without labelled genre-mates / artist-mates simply gets the audio model (its weight is not used)."""

from __future__ import annotations

from collections import defaultdict

import numpy as np

K_ARTIST, K_GENRE = 2.0, 5.0          # shrinkage: how many "average" tracks a prior starts with
K_RULE = 3.0                           # how many "typical" tracks a genre -> mood hint is worth

# known pairs (the user's rule): these genres sound heavy
GENRE_HINTS = {g: ["Тяжёлое"] for g in ("metal", "metalcore", "deathcore", "djent", "nu metal", "post-hardcore",
                                         "hardcore", "punk", "heavy metal", "power metal", "thrash metal", "industrial metal",
                                         # the catalogue's names since the genre vocabulary (core/genres.py)
                                         "метал", "металкор", "джент", "ню-метал", "пост-хардкор", "хардкор", "панк",
                                         "пауэр-метал")}


class Neighbours:
    def __init__(self, db, moods: list[str]) -> None:
        from .mood import _artist_key
        self.moods = moods
        idx = {m: j for j, m in enumerate(moods)}
        self.labels: dict[int, np.ndarray] = {}
        for tid, ms in db.mood_training_set():
            v = np.zeros(len(moods))
            for m in ms:
                if m in idx:
                    v[idx[m]] = 1.0
            self.labels[tid] = v
        self.base = np.mean(list(self.labels.values()), axis=0) if self.labels else np.zeros(len(moods))
        self.artist_of: dict[int, str] = {}
        self.genres_of: dict[int, list[str]] = defaultdict(list)
        for r in db.conn.execute("SELECT id, COALESCE(NULLIF(albumartist,''), artist) FROM tracks WHERE missing=0"):
            self.artist_of[r[0]] = (r[1] or "").casefold()
        for tid, g in db.conn.execute("SELECT track_id, genre FROM track_genres"):
            self.genres_of[tid].append(g.casefold())        # SQLite's lower() leaves Cyrillic as it is
        self.a_sum: dict[str, np.ndarray] = defaultdict(lambda: np.zeros(len(moods)))
        self.a_n: dict[str, int] = defaultdict(int)
        self.g_sum: dict[str, np.ndarray] = defaultdict(lambda: np.zeros(len(moods)))
        self.g_n: dict[str, int] = defaultdict(int)
        self.ga_sum: dict[tuple, np.ndarray] = defaultdict(lambda: np.zeros(len(moods)))   # (genre, artist)
        self.ga_n: dict[tuple, int] = defaultdict(int)
        for tid, v in self.labels.items():
            a = self.artist_of.get(tid, "")
            self.a_sum[a] += v
            self.a_n[a] += 1
            for g in self.genres_of.get(tid, []):
                self.g_sum[g] += v
                self.g_n[g] += 1
                self.ga_sum[(g, a)] += v
                self.ga_n[(g, a)] += 1

    def artist_prior(self, tid: int) -> np.ndarray | None:
        a = self.artist_of.get(tid, "")
        s, n = self.a_sum.get(a), self.a_n.get(a, 0)
        own = self.labels.get(tid)
        if own is not None and n:
            s, n = s - own, n - 1
        if not n:
            return None
        return (s + K_ARTIST * self.base) / (n + K_ARTIST)

    def genre_prior(self, tid: int, without_artist: bool = False) -> np.ndarray | None:
        a = self.artist_of.get(tid, "")
        own = self.labels.get(tid)
        vecs = []
        for g in self.genres_of.get(tid, []):
            s, n = self.g_sum.get(g), self.g_n.get(g, 0)
            hint = self._hint(g)
            if not n and hint is None:
                continue
            s = s if s is not None else np.zeros(len(self.moods))
            if without_artist:                       # as if the artist were new: none of its tracks are labelled
                s, n = s - self.ga_sum.get((g, a), 0), n - self.ga_n.get((g, a), 0)
            elif own is not None:
                s, n = s - own, n - 1
            if hint is not None:                     # the genre -> mood hint counts as a few typical tracks
                s, n = s + K_RULE * hint, n + K_RULE
            if n > 0:
                vecs.append((s + K_GENRE * self.base) / (n + K_GENRE))
        return np.mean(vecs, axis=0) if vecs else None

    def _hint(self, genre: str) -> np.ndarray | None:
        moods = GENRE_HINTS.get(genre)
        if not moods:
            return None
        v = self.base.copy()
        for m in moods:
            if m in self.moods:
                v[self.moods.index(m)] = 0.9
        return v


def blend(P: np.ndarray, tids: list[int], nb: Neighbours, wg: float, wa: float, new_artist: bool = False) -> np.ndarray:
    out = P.copy()
    for i, tid in enumerate(tids):
        g = nb.genre_prior(tid, without_artist=new_artist) if wg else None
        a = None if (new_artist or not wa) else nb.artist_prior(tid)
        w_g = wg if g is not None else 0.0
        w_a = wa if a is not None else 0.0
        if w_g or w_a:
            out[i] = (1 - w_g - w_a) * P[i] + (w_g * g if g is not None else 0) + (w_a * a if a is not None else 0)
    return out


def fit_weights(db, moods: list[str], tids: list[int], Y: np.ndarray, P_grouped: np.ndarray, P_random: np.ndarray) -> dict:
    """Pick the genre weight on the new-artist scenario and the artist weight on the known-artist one."""
    from . import moodcv
    nb = Neighbours(db, moods)
    auc = lambda P: moodcv.macro_metrics(Y, P)["auc"]  # noqa: E731
    base_new, base_known = auc(P_grouped), auc(P_random)
    best_g = max((auc(blend(P_grouped, tids, nb, w, 0.0, new_artist=True)), w) for w in np.arange(0, 0.61, 0.05))
    wg = float(best_g[1]) if best_g[0] > base_new + 0.002 else 0.0
    best_a = max((auc(blend(P_random, tids, nb, wg, w)), w) for w in np.arange(0, 0.71, 0.05))
    wa = float(best_a[1]) if best_a[0] > auc(blend(P_random, tids, nb, wg, 0.0)) + 0.002 else 0.0
    return {"genre": round(wg, 2), "artist": round(wa, 2),
            "auc_new_before": round(base_new, 4), "auc_new_after": round(auc(blend(P_grouped, tids, nb, wg, 0.0, True)), 4),
            "auc_known_before": round(base_known, 4), "auc_known_after": round(auc(blend(P_random, tids, nb, wg, wa)), 4)}


# the user's rules about moods that go together: (condition on the track, mood that drives it, moods it pulls along)
MOOD_LINKS = [("inst", "Спокойное", ["Ночное", "Фоновое"])]   # a calm instrumental is night / background music


def links(db, moods: list[str], tids: list[int], P: np.ndarray) -> np.ndarray:
    """Raise linked moods: e.g. an instrumental track the model finds calm is also likely 'Ночное' and 'Фоновое'."""
    if not len(tids):
        return P
    idx = {m: j for j, m in enumerate(moods)}
    vocal = {}
    q = ",".join("?" * len(tids))
    for tid, v in db.conn.execute(f"SELECT id, vocal FROM tracks WHERE id IN ({q})", list(tids)):
        vocal[tid] = v
    out = P.copy()
    for cond, driver, pulled in MOOD_LINKS:
        if driver not in idx:
            continue
        d = idx[driver]
        for i, tid in enumerate(tids):
            if cond == "inst" and vocal.get(tid) != "inst":
                continue
            strength = out[i, d]
            if strength < 0.35:
                continue
            for m in pulled:
                if m in idx:
                    out[i, idx[m]] = max(out[i, idx[m]], 0.85 * strength)
    return out


def apply(db, model, tids: list[int], P: np.ndarray, nb: "Neighbours | None" = None) -> np.ndarray:
    """Blend the model's (calibrated) probabilities with the neighbour priors it was trained with, then the mood links."""
    w = (getattr(model, "info", None) or {}).get("prior") or {}
    wg, wa = w.get("genre", 0.0), w.get("artist", 0.0)
    if not len(tids):
        return P
    if wg or wa:
        nb = nb or Neighbours(db, model.moods)
        P = blend(P, tids, nb, wg, wa)
    return np.clip(links(db, model.moods, tids, P), 0.0, 1.0)
