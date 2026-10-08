"""Progress of background scripts (training, re-tagging the library ...) shown in the running app.

A script writes ``<home>/external_job.json`` through :func:`job_context`; the app polls the file and shows a
progress bar while it is fresh.  The file is removed when the script finishes (or ignored once it goes stale)."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from .jobs import JobContext

FILE = "external_job.json"
STALE_SEC = 30


def write(home: Path, title: str, done: int, total: int, message: str = "") -> None:
    p = Path(home) / FILE
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"title": title, "done": done, "total": total, "message": message,
                               "pid": os.getpid(), "updated": time.time()}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def read(home: Path) -> dict | None:
    """The running script's progress, or None (no file, unreadable or stale)."""
    try:
        d = json.loads((Path(home) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if time.time() - d.get("updated", 0) < STALE_SEC else None


def clear(home: Path) -> None:
    (Path(home) / FILE).unlink(missing_ok=True)


@contextmanager
def job_context(home: Path, title: str, log=print):
    """JobContext whose progress also goes to the app (and whose log lines go to ``log``)."""
    state = {"stage": title}

    def on_progress(done, total, message):
        write(home, state["stage"], done, total, message)

    def on_log(line):
        if log:
            log(line)
        if line.strip() and not line.startswith("  "):
            state["stage"] = f"{title}: {line.strip()[:60]}"

    write(home, title, 0, 0, "старт")
    try:
        yield JobContext(on_progress, on_log, min_interval=0.5)
    finally:
        clear(home)
