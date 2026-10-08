"""Audio playback controller: QMediaPlayer pair (ping-pong, preloads the next track for near-gapless
transitions), the play queue and play counting.  Everything the GUI and the MCP server do with
playback goes through this class."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioBuffer, QAudioBufferOutput, QAudioFormat, QAudioOutput, QMediaPlayer

from musicplayer.core.db import Database
from musicplayer.core.plays import PlayTracker
from musicplayer.core.queue import PlayQueue
from musicplayer.core.spectrum import to_mono_float

FORMATS = {QAudioFormat.SampleFormat.UInt8: "u8", QAudioFormat.SampleFormat.Int16: "i16",
           QAudioFormat.SampleFormat.Int32: "i32", QAudioFormat.SampleFormat.Float: "f32"}

log = logging.getLogger(__name__)
PRELOAD_BEFORE_END_MS = 8000


class _Deck:
    """One QMediaPlayer with its own audio output."""

    def __init__(self, parent: QObject) -> None:
        self.player = QMediaPlayer(parent)
        self.output = QAudioOutput(parent)
        self.player.setAudioOutput(self.output)
        self.taps = QAudioBufferOutput(parent)      # raw samples for the colour-music visualiser
        self.player.setAudioBufferOutput(self.taps)
        self.track_id: int | None = None


class PlayerController(QObject):
    track_changed = Signal(object)          # track dict or None
    state_changed = Signal(str)             # playing | paused | stopped
    position_changed = Signal(float, float)  # seconds, duration
    queue_changed = Signal()
    play_counted = Signal(int)              # track id
    error = Signal(str)
    samples = Signal(object, int)           # mono float32 block, sample rate (for visualisers)

    def __init__(self, db: Database, parent: QObject | None = None, volume: float = 0.7) -> None:
        super().__init__(parent)
        self.db = db
        self.queue = PlayQueue()
        self._decks = [_Deck(self), _Deck(self)]
        self._active = 0
        self._volume = volume
        self._state = "stopped"
        self.tracker = PlayTracker(self._on_counted)
        self.engine = "qt"
        for i, d in enumerate(self._decks):
            d.output.setVolume(volume)
            d.player.positionChanged.connect(lambda ms, i=i: self._on_position(i, ms))
            d.player.mediaStatusChanged.connect(lambda st, i=i: self._on_status(i, st))
            d.player.errorOccurred.connect(lambda e, msg, i=i: self._on_error(i, msg))
            d.taps.audioBufferReceived.connect(lambda buf, i=i: self._on_buffer(i, buf))
        self._preloaded: int | None = None   # track id loaded on the standby deck

    # ---- helpers ------------------------------------------------------
    @property
    def deck(self) -> _Deck:
        return self._decks[self._active]

    @property
    def standby(self) -> _Deck:
        return self._decks[1 - self._active]

    @property
    def current(self) -> dict | None:
        return self.queue.current

    @property
    def state(self) -> str:
        return self._state

    def _set_state(self, s: str) -> None:
        if s != self._state:
            self._state = s
            self.state_changed.emit(s)

    # ---- commands -----------------------------------------------------
    def play_tracks(self, tracks: list[dict], start: int = 0) -> None:
        self.queue.set(tracks, start)
        self.queue_changed.emit()
        self._start_current()

    def enqueue(self, tracks: list[dict], next_: bool = False) -> None:
        was_empty = not self.queue.items
        self.queue.add(tracks, next_)
        self.queue_changed.emit()
        self._preloaded = None
        if was_empty:
            self._start_current()

    def cue(self, tracks: list[dict], start: int = 0, pos: float = 0.0) -> None:
        """Restore a session: the queue and the current track at ``pos``, paused."""
        self.queue.set(tracks, start)
        self.queue_changed.emit()
        t = self.current
        if t is None:
            return
        self._load(self.deck, t)
        self._cue_pos = max(0.0, pos)
        self._cue_id = t["id"]
        self.tracker.start(t["id"], t.get("duration") or 0)
        self._set_state("paused")
        self.track_changed.emit(t)
        self.position_changed.emit(self._cue_pos, float(t.get("duration") or 0))

    def play(self) -> None:
        if self.current is None:
            return
        if self.deck.track_id is None:
            self._start_current()
        else:
            if getattr(self, "_cue_pos", 0) and getattr(self, "_cue_id", None) == (self.current or {}).get("id"):
                self.deck.player.setPosition(int(self._cue_pos * 1000))
            self._cue_pos, self._cue_id = 0.0, None
            self.deck.player.play()
            self._set_state("playing")

    def pause(self) -> None:
        self.deck.player.pause()
        self._set_state("paused")

    def toggle(self) -> None:
        self.pause() if self._state == "playing" else self.play()

    def stop(self) -> None:
        for d in self._decks:
            d.player.stop()
            d.track_id = None
        self._preloaded = None
        self._set_state("stopped")

    def next(self) -> None:
        t = self.queue.next(auto=False)
        self._start_current() if t else self.stop()

    def prev(self) -> None:
        if self.deck.player.position() > 3000:       # like most players: restart first
            self.seek(0)
            return
        self.queue.prev()
        self._start_current()

    def jump(self, queue_index: int) -> None:
        self.queue.jump(queue_index)
        self._start_current()

    def duration(self) -> float:
        """Length of the current track in seconds (engine value, else the tag value)."""
        d = self.deck.player.duration() / 1000
        t = self.current
        return d if d > 0 else float((t or {}).get("duration") or 0)

    def seek_fraction(self, fraction: float) -> float:
        """Seek to a fraction (0..1) of the current track; returns the target in seconds."""
        target = max(0.0, min(1.0, fraction)) * self.duration()
        self.seek(target)
        return target

    def seek(self, seconds: float) -> None:
        self.tracker.seek()
        self.deck.player.setPosition(int(max(0, seconds) * 1000))

    def set_volume(self, v: float) -> None:
        self._volume = max(0.0, min(1.0, v))
        for d in self._decks:
            d.output.setVolume(self._volume)

    @property
    def volume(self) -> float:
        return self._volume

    def drop_upcoming(self) -> None:
        """Forget the tracks queued after the current one (the current song keeps playing)."""
        self.queue.truncate_after_current()
        self._preloaded = None
        self.queue_changed.emit()

    def set_shuffle(self, mode: str) -> None:
        self.queue.set_shuffle(mode)
        self._preloaded = None
        self.queue_changed.emit()

    def set_repeat(self, mode: str) -> None:
        self.queue.set_repeat(mode)
        self._preloaded = None
        self.queue_changed.emit()

    # ---- internals ----------------------------------------------------
    def _load(self, deck: _Deck, track: dict) -> None:
        deck.track_id = track["id"]
        deck.player.setSource(QUrl.fromLocalFile(str(Path(track["path"]))))

    def _start_current(self) -> None:
        t = self.current
        if t is None:
            self.stop()
            self.track_changed.emit(None)
            return
        if self._preloaded == t["id"] and self.standby.track_id == t["id"]:
            self.deck.player.stop()
            self._active = 1 - self._active          # swap to the preloaded deck
        else:
            self.standby.player.stop()
            self._load(self.deck, t)
        self._preloaded = None
        self.tracker.start(t["id"], t.get("duration") or 0)
        self.deck.player.play()
        self._set_state("playing")
        self.track_changed.emit(t)
        self.queue_changed.emit()

    def _on_position(self, i: int, ms: int) -> None:
        if i != self._active:
            return
        dur_ms = self.deck.player.duration()
        self.tracker.update(ms / 1000, playing=self._state == "playing")
        self.position_changed.emit(ms / 1000, dur_ms / 1000)
        if dur_ms > 0 and dur_ms - ms < PRELOAD_BEFORE_END_MS and self._preloaded is None:
            nxt = self.queue.peek_next()
            if nxt and nxt["id"] != self.deck.track_id:
                self._load(self.standby, nxt)
                self._preloaded = nxt["id"]

    def _on_buffer(self, i: int, buf: QAudioBuffer) -> None:
        if i != self._active:
            return
        fmt = buf.format()
        kind = FORMATS.get(fmt.sampleFormat())
        if kind is None or not buf.isValid():
            return
        try:
            raw = bytes(buf.constData())[: buf.byteCount()]
            self.samples.emit(to_mono_float(raw, kind, max(1, fmt.channelCount())), fmt.sampleRate())
        except Exception:  # visual only; never disturb playback
            log.debug("sample decode failed", exc_info=True)

    def _on_status(self, i: int, status) -> None:
        if i != self._active or status != QMediaPlayer.MediaStatus.EndOfMedia:
            return
        t = self.queue.next(auto=True)
        if t is None:
            self.stop()
            self.track_changed.emit(None)
            return
        if self.queue.repeat == "one":
            self.deck.player.setPosition(0)
            self.tracker.start(t["id"], t.get("duration") or 0)
            self.deck.player.play()
            return
        self._start_current()

    def _on_error(self, i: int, msg: str) -> None:
        if i == self._active and msg:
            log.warning("player error: %s", msg)
            self.error.emit(msg)

    def _on_counted(self, track_id: int, listened: float) -> None:
        self.db.record_play(track_id, listened)
        for t in self.queue.items:
            if t["id"] == track_id:
                t["play_count"] = (t.get("play_count") or 0) + 1
        self.play_counted.emit(track_id)

    def shutdown(self) -> None:
        self.stop()

    def snapshot(self) -> dict:
        t = self.current
        return {"state": self._state, "volume": self._volume, "track": t,
                "position": self.deck.player.position() / 1000, "duration": self.deck.player.duration() / 1000,
                "engine": self.engine, "listened": self.tracker.listened, "counted": self.tracker.counted,
                "queue": self.queue.snapshot()}
