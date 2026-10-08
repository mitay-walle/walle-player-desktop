"""Check the mood model against moods found on the internet (AcousticBrainz), on tracks the user did NOT label.

AcousticBrainz gives per-recording probabilities for sad / happy / aggressive / relaxed / party.  Only confident
values count as truth (>= ``strong`` -> the mood fits, <= ``weak`` -> it does not); the model's probability for the
matching mood is then scored by ROC AUC per mood."""

from __future__ import annotations

import numpy as np

from .db import Database
from .mood import MoodModel, row_for
from .webtags import _ensure, get_web

# AcousticBrainz class -> moods of the app it is evidence for
AB_TO_MOODS = {"mood_sad": ["Грустное"], "mood_happy": ["Весёлое"], "mood_aggressive": ["Злое"],
               "mood_relaxed": ["Спокойное"], "mood_party": ["Танцевальное", "Энергичное"]}


def web_truth(db: Database, strong: float = 0.7, weak: float = 0.3) -> dict[str, dict[int, int]]:
    """{app mood: {track id: 1 | 0}} from AcousticBrainz, only tracks without the user's own labels."""
    _ensure(db)
    labelled = {t for t, _ in db.mood_training_set()}
    out: dict[str, dict[int, int]] = {}
    for (tid,) in db.conn.execute("SELECT track_id FROM track_web WHERE length(ab) > 2"):
        if tid in labelled:
            continue
        ab = get_web(db, tid)["ab"]
        for k, moods in AB_TO_MOODS.items():
            p = ab.get(k)
            if p is None or weak < p < strong:
                continue
            for m in moods:
                out.setdefault(m, {})[tid] = int(p >= strong)
    return out


def agreement(db: Database, model: MoodModel, truth: dict[str, dict[int, int]] | None = None, min_each: int = 5) -> dict:
    """{'per_mood': {mood: {auc, n, pos}}, 'mean_auc', 'tracks'} of the model against web truth."""
    from sklearn.metrics import roc_auc_score
    truth = truth if truth is not None else web_truth(db)
    mode, vocab = model.info.get("mode", "descriptors"), model.info.get("web_vocab")
    ids = sorted({t for d in truth.values() for t in d})
    rows = {t: row_for(db, t, mode, vocab, (model.info or {}).get("feat_v", 1)) for t in ids}
    rows = {t: r for t, r in rows.items() if r is not None}
    if not rows:
        return {"per_mood": {}, "mean_auc": None, "tracks": 0}
    order = list(rows)
    P = model.predict_proba(np.vstack([rows[t] for t in order]))
    pos = {t: i for i, t in enumerate(order)}
    per = {}
    for mood, labels in truth.items():
        if mood not in model.moods:
            continue
        j = model.moods.index(mood)
        pairs = [(P[pos[t], j], y) for t, y in labels.items() if t in pos]
        ys = [y for _, y in pairs]
        if sum(ys) < min_each or len(ys) - sum(ys) < min_each:
            continue
        per[mood] = {"auc": round(float(roc_auc_score(ys, [p for p, _ in pairs])), 3), "n": len(ys), "pos": int(sum(ys))}
    mean = round(float(np.mean([v["auc"] for v in per.values()])), 3) if per else None
    return {"per_mood": per, "mean_auc": mean, "tracks": len(rows)}
