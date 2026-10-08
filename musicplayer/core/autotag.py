"""Automatic mood for new tracks: scan -> features -> trained model -> labels.

A track that has no label yet gets the signals its model needs (audio descriptors, CLAP embedding, internet tags),
then the model predicts: moods that pass the per-mood threshold of the chosen strictness become the track's moods (source 'auto', shown
with a mark and never used for training), weaker guesses stay as suggestions.  The user can confirm or correct them
(👍/👎 in "My wave", the review list, the labelling feed); confirmed ones become training data on the next retrain.
"""

from __future__ import annotations

from pathlib import Path

from . import clap, mood, webtags
from .db import Database
from .jobs import JobContext, null_context



def pending_ids(db: Database) -> list[int]:
    """Tracks the model has not looked at yet: no label of any kind and no model suggestion."""
    return [r[0] for r in db.conn.execute(
        "SELECT t.id FROM tracks t WHERE t.missing=0 AND NOT EXISTS(SELECT 1 FROM track_moods m WHERE m.track_id=t.id "
        "AND m.source IN ('manual','seed','auto','model'))")]


def tag_new_tracks(db: Database, models_dir: str | Path, ctx: JobContext | None = None, ids: list[int] | None = None,
                   auto_mode: str = "balanced", workers: int = 4) -> dict:
    ctx = ctx or null_context()
    model = mood.load_model(models_dir)
    if model is None:
        raise ValueError("Модель настроений ещё не обучена")
    ids = pending_ids(db) if ids is None else list(ids)
    if not ids:
        return {"tracks": 0, "tagged": 0, "auto": 0, "suggested": 0, "skipped": 0}
    mode = model.info.get("mode", "descriptors")
    ctx.log(f"Автоопределение настроения: {len(ids)} новых треков (признаки модели: {mode})")
    steps = []
    if mode in ("descriptors", "both"):
        steps.append("дескрипторы")
        mood.analyze_tracks(db, ids, ctx, workers=workers)
    if mode in ("clap", "both"):
        if clap.available():
            steps.append("CLAP")
            clap.embed_tracks(db, ids, ctx)
        else:
            ctx.log("CLAP недоступен — эти треки пока не определить: " + clap.unavailable_reason())
    if model.info.get("web_vocab") is not None:
        steps.append("интернет-теги")
        webtags.fetch_tracks(db, ids, ctx)
    ctx.check()
    r = mood.predict(db, models_dir, ids, ctx=ctx, only_unlabelled=True, auto_mode=auto_mode)
    auto = db.conn.execute(f"SELECT COUNT(DISTINCT track_id) FROM track_moods WHERE source='auto' AND track_id IN "
                           f"({','.join('?' * len(ids))})", ids).fetchone()[0]
    out = {"tracks": len(ids), "tagged": r["predicted"], "auto": auto, "suggested": r["predicted"] - auto,
           "skipped": r["skipped_no_descriptors"], "steps": steps}
    ctx.log(f"Готово: настроение определено у {r['predicted']} из {len(ids)} (автоматически {auto}, предложениями "
            f"{r['predicted'] - auto}); без нужных признаков пропущено {r['skipped_no_descriptors']}")
    return out
