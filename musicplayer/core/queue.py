"""Play queue with shuffle (plain or biased towards rarely played tracks) and repeat; Qt-free."""

from __future__ import annotations

import random

from .db import rare_weights


class PlayQueue:
    REPEAT = ("off", "all", "one")
    SHUFFLE = ("off", "random", "rare")

    def __init__(self, rng: random.Random | None = None) -> None:
        self.items: list[dict] = []
        self.pos = -1                      # index into ``order``
        self.order: list[int] = []         # playback order: indices into ``items``
        self.shuffle = "off"
        self.repeat = "off"
        self._rng = rng or random.Random()

    # ---- content ------------------------------------------------------
    def set(self, tracks: list[dict], start: int = 0) -> dict | None:
        self.items = list(tracks)
        start = start if 0 <= start < len(tracks) else 0
        self._rebuild(first=start if tracks else None)
        self.pos = (start if self.shuffle == "off" else 0) if tracks else -1
        return self.current

    def add(self, tracks: list[dict], next_: bool = False) -> None:
        n0 = len(self.items)
        self.items += list(tracks)
        new = list(range(n0, len(self.items)))
        if next_ and self.pos >= 0:
            at = self.pos + 1
            self.order[at:at] = new
        else:
            self.order += new
        if self.pos < 0 and self.items:
            self.pos = 0

    def remove(self, index: int) -> None:
        """Remove ``items[index]`` (an index into items, not order)."""
        if not 0 <= index < len(self.items):
            return
        cur_item = self.order[self.pos] if self.pos >= 0 else None
        del self.items[index]
        self.order = [i - (i > index) for i in self.order if i != index]
        if cur_item is None or cur_item == index:
            self.pos = min(self.pos, len(self.order) - 1)
        else:
            self.pos = self.order.index(cur_item - (cur_item > index))

    def truncate_after_current(self) -> None:
        """Drop everything that would play after the current track (the played history stays)."""
        if self.pos < 0:
            return
        keep = self.order[:self.pos + 1]
        self.items = [self.items[i] for i in keep]
        self.order = list(range(len(self.items)))
        self.pos = len(self.items) - 1

    def clear(self) -> None:
        self.items, self.order, self.pos = [], [], -1

    # ---- navigation ---------------------------------------------------
    @property
    def current(self) -> dict | None:
        return self.items[self.order[self.pos]] if 0 <= self.pos < len(self.order) else None

    @property
    def current_index(self) -> int:
        return self.order[self.pos] if 0 <= self.pos < len(self.order) else -1

    def peek_next(self) -> dict | None:
        """The track that :meth:`next` would start (used to preload for gapless)."""
        if not self.order:
            return None
        if self.repeat == "one":
            return self.current
        if self.pos + 1 < len(self.order):
            return self.items[self.order[self.pos + 1]]
        return self.items[self.order[0]] if self.repeat == "all" else None

    def next(self, auto: bool = False) -> dict | None:
        """Advance. ``auto`` = track ended by itself (repeat-one replays; otherwise manual skip moves on)."""
        if not self.order:
            return None
        if auto and self.repeat == "one":
            return self.current
        if self.pos + 1 < len(self.order):
            self.pos += 1
        elif self.repeat == "all":
            if self.shuffle != "off":
                self._rebuild(first=None)
            self.pos = 0
        else:
            return None
        return self.current

    def prev(self) -> dict | None:
        if not self.order:
            return None
        self.pos = max(0, self.pos - 1) if self.pos > 0 else (len(self.order) - 1 if self.repeat == "all" else 0)
        return self.current

    def jump(self, index: int) -> dict | None:
        """Play ``items[index]`` now."""
        if index in self.order:
            self.pos = self.order.index(index)
        return self.current

    # ---- modes --------------------------------------------------------
    def set_shuffle(self, mode: str) -> None:
        if mode not in self.SHUFFLE:
            raise ValueError(mode)
        cur = self.current_index
        self.shuffle = mode
        self._rebuild(first=cur if cur >= 0 else None)
        self.pos = (max(cur, 0) if mode == "off" else 0) if self.order else -1

    def set_repeat(self, mode: str) -> None:
        if mode not in self.REPEAT:
            raise ValueError(mode)
        self.repeat = mode

    def _rebuild(self, first: int | None) -> None:
        """Rebuild ``order``; the track ``first`` (items index) is placed at the front."""
        idx = list(range(len(self.items)))
        if self.shuffle == "off":
            self.order = idx
            return
        rest = [i for i in idx if i != first]
        if self.shuffle == "rare":
            w = rare_weights([self.items[i].get("play_count") or 0 for i in rest])
            keyed = sorted(((self._rng.random() ** (1 / wi), i) for wi, i in zip(w, rest)), reverse=True)
            rest = [i for _, i in keyed]
        else:
            self._rng.shuffle(rest)
        self.order = ([first] if first is not None else []) + rest

    def snapshot(self) -> dict:
        return {"index": self.current_index, "position": self.pos, "shuffle": self.shuffle,
                "repeat": self.repeat, "length": len(self.items),
                "tracks": [{"queue_index": i, "id": t["id"], "title": t["title"], "artist": t["artist"],
                            "current": i == self.current_index} for i, t in enumerate(self.items)],
                "upcoming": [self.items[i]["id"] for i in self.order[self.pos + 1:self.pos + 6]]}
