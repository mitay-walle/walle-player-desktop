"""Heavy jobs that run in a separate PROCESS, so the player window and the library server never stall.

A job running in a thread of the app shares Python's GIL with the GUI and the server: scanning (mutagen, covers),
training (scikit-learn cross-validation) and re-tagging the library made both stutter or freeze.  Here they run in a
child process with their own database connection; progress, log lines, metrics and the result travel back through a
queue (see ``JobManager.run_process``).

Every job is ``fn(ctx, emit, **kwargs)`` with picklable kwargs and a picklable result.
"""

from __future__ import annotations

import importlib
import time
import traceback
from pathlib import Path

from .jobs import CancelledError, CancelToken, JobContext


class EventToken(CancelToken):
    """A cancel token backed by a multiprocessing Event (shared by the app and the child process)."""

    def __init__(self, event) -> None:
        super().__init__()
        self._mp_event = event

    def cancel(self) -> None:
        super().cancel()
        self._mp_event.set()

    @property
    def cancelled(self) -> bool:
        return self._mp_event.is_set() or super().cancelled


def entry(func_path: str, kwargs: dict, queue, cancel_event) -> None:
    """Child-process entry point: run ``module:function`` and report back through ``queue``."""
    try:
        mod, _, name = func_path.partition(":")
        fn = getattr(importlib.import_module(mod), name)
        ctx = JobContext(lambda d, t, m: queue.put(("p", d, t, m)), lambda m: queue.put(("l", m)),
                         EventToken(cancel_event), min_interval=0.1)
        result = fn(ctx, lambda m: queue.put(("m", m)), **kwargs)
        queue.put(("ok", result))
    except CancelledError:
        queue.put(("cancel",))
    except BaseException as e:  # noqa: BLE001 - reported to the app, never a crash
        queue.put(("err", f"{e}", traceback.format_exc()))


# ---- the jobs -----------------------------------------------------------------------------------------------
def _db(db_path: str):
    from .db import Database
    return Database(Path(db_path))


def scan(ctx: JobContext, emit, db_path: str, root: str, covers: str, full: bool = False) -> dict:
    from . import scanner
    db = _db(db_path)
    try:
        ctx.log(f"Сканирование {root}")
        r = scanner.scan(db, root, covers, lambda i, p: (ctx.check(), ctx.progress(i, 0, p)), full=full)
        ctx.log(f"Готово: новых {r['added']}, обновлено {r['updated']}, пропало {r['missing']}, "
                f"ошибок {len(r['errors'])}, всего {r['total']}")
        return r
    finally:
        db.close()


def backup_model(models_dir) -> None:
    """Keep the current model as models/history/mood_model-<date>.pkl before it is replaced."""
    import shutil
    cur = Path(models_dir) / "mood_model.pkl"
    if cur.exists():
        hist = Path(models_dir) / "history"
        hist.mkdir(exist_ok=True)
        shutil.copy2(cur, hist / time.strftime("mood_model-%Y%m%d-%H%M%S", time.localtime(cur.stat().st_mtime)) + ".pkl")


def train(ctx: JobContext, emit, db_path: str, models: str, epochs: int, features: str = "auto", web="auto") -> dict:
    from . import mood
    backup_model(models)
    db = _db(db_path)
    try:
        return mood.train(db, models, ctx, epochs=epochs, on_epoch=lambda m: emit({"train": m}),
                          features=features, on_cv=lambda rows: emit({"cv": rows}), web=web)
    finally:
        db.close()


def predict(ctx: JobContext, emit, db_path: str, models: str, threshold: float, only_unlabelled: bool = True) -> dict:
    from . import mood
    db = _db(db_path)
    try:
        r = mood.predict(db, models, None, threshold, ctx, only_unlabelled=only_unlabelled)
        db.conn.commit()
        return r
    finally:
        db.close()


def autotag(ctx: JobContext, emit, db_path: str, models: str, ids: list[int] | None, mode: str) -> dict:
    from . import autotag as at
    db = _db(db_path)
    try:
        r = at.tag_new_tracks(db, models, ctx, ids, mode)
        db.conn.commit()
        return r
    finally:
        db.close()
