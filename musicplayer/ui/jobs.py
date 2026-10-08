"""Background jobs with live progress for the GUI.  Callbacks always arrive in the GUI thread."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from musicplayer.core.jobs import CancelledError, CancelToken, JobContext

log = logging.getLogger(__name__)


@dataclass
class JobInfo:
    kind: str
    title: str
    started: float = field(default_factory=time.time)
    done: int = 0
    total: int = 0
    message: str = ""
    state: str = "running"          # running | finished | failed | cancelled
    result: Any = None
    error: str = ""
    logs: list[str] = field(default_factory=list)
    token: CancelToken = field(default_factory=CancelToken)


class JobManager(QObject):
    """One job at a time.  Signals: started(JobInfo), progress(JobInfo), log(JobInfo, str),
    metric(JobInfo, dict) for live numbers such as training loss, finished(JobInfo)."""

    started = Signal(object)
    progress = Signal(object)
    log = Signal(object, str)
    metric = Signal(object, dict)
    finished = Signal(object)
    # internal thread -> GUI hops
    _prog = Signal(object, int, int, str)
    _log = Signal(object, str)
    _metric = Signal(object, dict)
    _end = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.current: JobInfo | None = None
        self.history: list[JobInfo] = []
        self._closed = False
        self._prog.connect(self._on_prog)
        self._log.connect(self._on_log)
        self._metric.connect(lambda j, m: None if self._closed else self.metric.emit(j, m))
        self._end.connect(self._on_end)

    @property
    def busy(self) -> bool:
        return self.current is not None and self.current.state == "running"

    def run(self, kind: str, title: str, fn: Callable[[JobContext, Callable[[dict], None]], Any]) -> JobInfo:
        """``fn(ctx, emit_metric)`` runs in a worker thread; its return value becomes ``JobInfo.result``."""
        if self.busy:
            raise RuntimeError(f"Занято: выполняется «{self.current.title}»")
        job = JobInfo(kind, title)
        self.current = job
        ctx = JobContext(lambda d, t, m: self._prog.emit(job, d, t, m), lambda m: self._log.emit(job, m),
                         job.token, min_interval=0.05)

        def work() -> None:
            try:
                job.result = fn(ctx, lambda m: self._metric.emit(job, m))
                job.state = "finished"
            except CancelledError:
                job.state = "cancelled"
            except Exception as e:  # shown in the UI, never crashes the app
                log.exception("job %s failed", kind)
                job.state, job.error = "failed", str(e)
            self._end.emit(job)

        self.started.emit(job)
        threading.Thread(target=work, name=f"job-{kind}", daemon=True).start()
        return job

    def run_process(self, kind: str, title: str, func_path: str, kwargs: dict, fallback=None) -> JobInfo:
        """Run ``module:function`` (see core/procjobs.py) in a child PROCESS: the GUI and the library server keep
        their CPU and the GIL while scanning / training.  Same signals as :meth:`run`.

        With MUSICPLAYER_JOBS_INPROC=1 (tests) the function runs in a thread instead."""
        import os
        if os.environ.get("MUSICPLAYER_JOBS_INPROC") == "1":
            import importlib
            mod, _, name = func_path.partition(":")
            fn = getattr(importlib.import_module(mod), name)
            return self.run(kind, title, lambda jc, emit: fn(jc, emit, **kwargs))
        if self.busy:
            raise RuntimeError(f"Занято: выполняется «{self.current.title}»")
        import multiprocessing as mp
        from musicplayer.core.procjobs import EventToken, entry
        mpc = mp.get_context("spawn")
        queue, event = mpc.Queue(), mpc.Event()
        job = JobInfo(kind, title)
        job.token = EventToken(event)
        self.current = job
        # not a daemon: the job may start its own worker pool (audio analysis); shutdown() stops it explicitly
        proc = mpc.Process(target=entry, args=(func_path, kwargs, queue, event), name=f"job-{kind}", daemon=False)
        self._proc = proc

        def pump() -> None:
            import queue as q
            done = False
            last = time.time()
            while not done:
                try:
                    msg = queue.get(timeout=0.25)
                except q.Empty:
                    if not proc.is_alive():
                        try:                         # the last messages may still be in the pipe
                            msg = queue.get(timeout=1.0)
                        except q.Empty:
                            job.state, job.error = "failed", f"процесс задачи завершился (код {proc.exitcode})"
                            break
                    elif job.token.cancelled and time.time() - last > 15:
                        proc.terminate()             # did not stop by itself in 15 s
                        job.state = "cancelled"
                        break
                    else:
                        continue
                last = time.time()
                kind_ = msg[0]
                if kind_ == "p":
                    self._prog.emit(job, msg[1], msg[2], msg[3])
                elif kind_ == "l":
                    self._log.emit(job, msg[1])
                elif kind_ == "m":
                    self._metric.emit(job, msg[1])
                elif kind_ == "ok":
                    job.result, job.state, done = msg[1], "finished", True
                elif kind_ == "cancel":
                    job.state, done = "cancelled", True
                elif kind_ == "err":
                    log.error("job %s failed in its process:\n%s", kind, msg[2])
                    job.state, job.error, done = "failed", msg[1], True
            proc.join(timeout=5)
            self._end.emit(job)

        self.started.emit(job)
        proc.start()
        threading.Thread(target=pump, name=f"job-{kind}-pump", daemon=True).start()
        return job

    def shutdown(self) -> None:
        """Cancel the running job and drop its late signals (the database is about to be closed)."""
        self._closed = True
        if self.current is not None:
            self.current.token.cancel()
        proc = getattr(self, "_proc", None)
        if proc is not None and proc.is_alive():
            proc.join(timeout=3)
            if proc.is_alive():
                proc.terminate()

    def cancel(self) -> None:
        if self.busy:
            self.current.token.cancel()

    def _on_prog(self, job: JobInfo, done: int, total: int, msg: str) -> None:
        if self._closed:
            return
        job.done, job.total, job.message = done, total, msg
        self.progress.emit(job)

    def _on_log(self, job: JobInfo, msg: str) -> None:
        if self._closed:
            return
        job.logs.append(msg)
        self.log.emit(job, msg)

    def _on_end(self, job: JobInfo) -> None:
        if self._closed:
            return
        self.history.append(job)
        self.finished.emit(job)
