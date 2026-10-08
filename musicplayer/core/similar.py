"""Similar tracks.

Two signals are blended:

* audio similarity: cosine similarity of standardized vectors (CLAP embeddings when present,
  otherwise Freesound-style descriptors), computed once the library has been analysed;
* metadata similarity: shared genres, moods, artist, album and close years - works right
  after the first scan, before any audio analysis.
"""

from __future__ import annotations

import numpy as np

from .db import Database

EMB_MODEL = "clap"


def _vectors(db: Database, ids: list[int]) -> tuple[list[int], np.ndarray | None]:
    """Embedding matrix for ids that have one (rows aligned with the returned id list)."""
    got, rows = [], []
    for tid in ids:
        blob = db.get_embedding(tid, EMB_MODEL)
        if blob:
            got.append(tid)
            rows.append(np.frombuffer(blob, dtype=np.float32))
    return got, (np.vstack(rows) if rows else None)


def audio_neighbors(db: Database, track_id: int, candidate_ids: list[int], k: int = 50) -> dict[int, float]:
    q = db.get_embedding(track_id, EMB_MODEL)
    if not q:
        return {}
    ids, mat = _vectors(db, candidate_ids)
    if mat is None:
        return {}
    qv = np.frombuffer(q, dtype=np.float32)
    mat = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
    sims = mat @ (qv / (np.linalg.norm(qv) + 1e-9))
    order = np.argsort(-sims)[:k]
    return {ids[i]: float(sims[i]) for i in order if ids[i] != track_id}


def metadata_score(a: dict, b: dict) -> float:
    s = 0.0
    ga, gb = {g.lower() for g in a["genres"]}, {g.lower() for g in b["genres"]}
    if ga and gb:
        s += 0.4 * len(ga & gb) / len(ga | gb)
    ma, mb = set(a["moods"]), set(b["moods"])
    if ma and mb:
        s += 0.3 * len(ma & mb) / len(ma | mb)
    if (a["artist"] or "").lower() == (b["artist"] or "").lower():
        s += 0.15
    if a["album"] and a["album"] == b["album"]:
        s += 0.05
    if a["year"] and b["year"]:
        s += 0.1 * max(0.0, 1 - abs(a["year"] - b["year"]) / 20)
    return s


def similar_tracks(db: Database, track_id: int, limit: int = 20, same_artist: bool = True) -> list[dict]:
    """Tracks most similar to ``track_id``; each result carries ``similarity`` (0..1) and ``basis``."""
    src = db.get_track(track_id)
    if src is None:
        return []
    pool = db.search(limit=10**9, order="artist")
    pool = [t for t in pool if t["id"] != track_id]
    if not same_artist:
        pool = [t for t in pool if (t["artist"] or "").lower() != (src["artist"] or "").lower()]
    audio = audio_neighbors(db, track_id, [t["id"] for t in pool], k=len(pool))
    out = []
    for t in pool:
        meta = metadata_score(src, t)
        if t["id"] in audio:
            score, basis = 0.7 * max(audio[t["id"]], 0) + 0.3 * meta, "audio+tags"
        else:
            score, basis = meta, "tags"
        if score > 0:
            out.append({**t, "similarity": round(score, 4), "basis": basis})
    out.sort(key=lambda r: r["similarity"], reverse=True)
    return out[:limit]
