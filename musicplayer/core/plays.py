"""Play counting: a play counts once listened time exceeds 50% of the track or 4 minutes."""

from __future__ import annotations

MAX_LISTEN_SEC = 240.0
MAX_JUMP_SEC = 3.0  # position deltas above this are seeks, not listening


class PlayTracker:
    """Feed it position updates; ``on_count(track_id, listened)`` fires once per track start."""

    def __init__(self, on_count, threshold: float = 0.5, max_sec: float = MAX_LISTEN_SEC) -> None:
        self.on_count = on_count
        self.threshold = threshold
        self.max_sec = max_sec
        self.track_id: int | None = None
        self.duration = 0.0
        self.listened = 0.0
        self.counted = False
        self._last: float | None = None

    def start(self, track_id: int, duration: float) -> None:
        self.track_id, self.duration = track_id, duration or 0.0
        self.listened, self.counted, self._last = 0.0, False, None

    def update(self, position: float, playing: bool = True) -> None:
        if self.track_id is None:
            return
        if playing and self._last is not None:
            delta = position - self._last
            if 0 < delta <= MAX_JUMP_SEC:
                self.listened += delta
        self._last = position
        if self.counted or self.listened <= 0:
            return
        limit = self.max_sec if self.duration <= 0 else min(self.threshold * self.duration, self.max_sec)
        if self.listened > limit or self.listened >= self.max_sec:
            self.counted = True
            self.on_count(self.track_id, self.listened)

    def seek(self) -> None:
        """Call after a user seek so the jump is not counted as listening."""
        self._last = None
