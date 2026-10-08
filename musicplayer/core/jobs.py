"""Qt-free job primitives shared by every long-running core function.

A core function receives a :class:`JobContext`, reports progress through it,
and calls :meth:`JobContext.check` at safe points so cancellation raises
:class:`CancelledError` and propagates (never partial results).
"""

from __future__ import annotations

import threading
import time
from typing import Callable

ProgressCallback = Callable[[int, int, str], None]
LogCallback = Callable[[str], None]


class CancelledError(Exception):
    """Raised by :meth:`JobContext.check` once the job was cancelled."""


class JobBusyError(RuntimeError):
    """Raised when an exclusive job is requested while another one runs."""


class CancelToken:
    """Thread-safe cancellation flag."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


class JobContext:
    """Progress / log / cancel channel handed to core functions.

    ``progress`` is rate-limited to one call per ``min_interval`` seconds, except
    for the first call, the final call (``done == total``) and indeterminate
    updates (``total == 0``).
    """

    def __init__(
        self,
        progress_cb: ProgressCallback | None = None,
        log_cb: LogCallback | None = None,
        cancel_token: CancelToken | None = None,
        min_interval: float = 0.05,
    ) -> None:
        self._progress_cb = progress_cb
        self._log_cb = log_cb
        self._token = cancel_token or CancelToken()
        self._min_interval = max(0.0, float(min_interval))
        self._last_emit = 0.0
        self._last_args: tuple[int, int, str] | None = None

    # ---------------------------------------------------------------- reporting
    def progress(self, done: int, total: int, message: str = "") -> None:
        now = time.monotonic()
        final = total <= 0 or done >= total
        if not final and self._last_args is not None and (now - self._last_emit) < self._min_interval:
            return
        self._last_emit = now
        self._last_args = (done, total, message)
        if self._progress_cb is not None:
            self._progress_cb(done, total, message)

    def log(self, message: str) -> None:
        if self._log_cb is not None:
            self._log_cb(message)

    # ---------------------------------------------------------------- cancel
    @property
    def token(self) -> CancelToken:
        return self._token

    @property
    def cancelled(self) -> bool:
        return self._token.cancelled

    def check(self) -> None:
        if self._token.cancelled:
            raise CancelledError("job cancelled")


def null_context() -> JobContext:
    """A context that reports nowhere (for scripts and tests)."""
    return JobContext()
