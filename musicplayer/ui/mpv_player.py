"""libmpv playback engine with real gapless transitions.

One mpv instance holds a two-entry playlist: the current track and the next one from the queue, so mpv
opens the next file ahead of time and joins them without a gap.  When mpv moves on by itself the queue
advances, the finished entry is removed and the following track is appended.  The public interface is the
one of :class:`PlayerController` (the GUI and MCP do not care which engine runs).

mpv gives no PCM, so for the colour-music visualiser the file is decoded in a background thread and
sliced at the playback position (same spectrum, any engine).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from musicplayer.core.db import Database
from musicplayer.core.plays import PlayTracker
from musicplayer.core.queue import PlayQueue
from musicplayer.ui.player import PlayerController

log = logging.getLogger(__name__)
SAMPLE_MAX_SEC = 900


def find_libmpv() -> Path | None:
    """Folder that contains libmpv-2.dll (env MUSICPLAYER_MPV_DIR, frozen bundle, exe dir, project third_party)."""
    cands = [os.environ.get("MUSICPLAYER_MPV_DIR"), getattr(sys, "_MEIPASS", None),
             str(Path(sys.executable).parent), str(Path(__file__).resolve().parents[2] / "third_party" / "mpv")]
    for c in cands:
        if c and (Path(c) / "libmpv-2.dll").exists():
            return Path(c)
    return None


def mpv_available() -> bool:
    d = find_libmpv()
    if d is None:
        return False
    os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
    try:
        os.add_dll_directory(str(d))
    except (AttributeError, OSError):
        pass
    try:
        import mpv  # noqa: F401
        return True
    except Exception:
        log.exception("libmpv found but python-mpv cannot load it")
        return False


def _end_reason(ev) -> str:
    """'eof' | 'stop' | 'error' ... from a python-mpv end-file event."""
    try:
        d = ev.as_dict()
        r = d.get("reason") or (d.get("event") or {}).get("reason") if isinstance(d.get("event"), dict) else d.get("reason")
        return r.decode() if isinstance(r, bytes) else str(r or "")
    except Exception:
        return ""


class MpvPlayerController(PlayerController):
    _pos_sig = Signal(float)
    _dur_sig = Signal(float)
    _plpos_sig = Signal(object)
    _end_sig = Signal(str)
    _pcm_ready = Signal(object, int, int)

    def __init__(self, db: Database, parent: QObject | None = None, volume: float = 0.7) -> None:
        QObject.__init__(self, parent)          # not PlayerController.__init__: no QMediaPlayer decks
        import mpv
        self.db = db
        self.queue = PlayQueue()
        self._volume = volume
        self._state = "stopped"
        self.tracker = PlayTracker(self._on_counted)
        self.engine = "mpv"
        self._closed = False
        self._loaded: list[int] = []            # queue-item track ids in mpv's playlist, current first
        self._pos = 0.0
        self._dur = 0.0
        self._pos_stamp = time.monotonic()
        self._pcm: np.ndarray | None = None
        self._pcm_sr = 44100
        self._pcm_id: int | None = None
        self.mpv = mpv.MPV(video="no", gapless_audio="yes", prefetch_playlist="yes", idle="yes", keep_open="no",
                           volume=int(volume * 100), input_default_bindings=False, input_vo_keyboard=False,
                           audio_display="no", force_window="no", load_scripts="no", ytdl="no", osc="no", config="no",
                           load_unsafe_playlists="no", load_stats_overlay="no", load_console="no", load_osd_console="no",
                           load_auto_profiles="no", load_select="no", load_positioning="no", load_commands="no",
                           load_context_menu="no",
                           **({"ao": os.environ["MUSICPLAYER_MPV_AO"]} if os.environ.get("MUSICPLAYER_MPV_AO") else {}))
        self._pos_sig.connect(self._on_pos)
        self._dur_sig.connect(lambda d: setattr(self, "_dur", d))
        self._plpos_sig.connect(self._on_plpos)
        self._end_sig.connect(self._on_end_file)
        self._pcm_ready.connect(self._set_pcm)
        self.mpv.observe_property("time-pos", lambda n, v: self._emit(self._pos_sig, float(v)) if v is not None else None)
        self.mpv.observe_property("duration", lambda n, v: self._emit(self._dur_sig, float(v or 0)))
        self.mpv.observe_property("playlist-pos", lambda n, v: self._emit(self._plpos_sig, v))
        self.mpv.event_callback("end-file")(lambda ev: self._emit(self._end_sig, _end_reason(ev)))
        self.vis = QTimer(self, interval=33)
        self.vis.timeout.connect(self._emit_samples)
        self.vis.start()

    def _emit(self, sig, *args) -> None:
        """Emit from mpv/worker threads unless the engine is shutting down."""
        if not self._closed:
            try:
                sig.emit(*args)
            except RuntimeError:        # QObject already deleted
                pass

    def shutdown(self) -> None:
        """Stop timers/threads' signals first, then destroy mpv (avoids callbacks into dead Qt objects)."""
        self._closed = True
        self.vis.stop()
        try:
            self.mpv.terminate()
        except Exception:
            pass

    # ---- the PlayerController interface -------------------------------
    def _path(self, t: dict) -> str:
        return str(Path(t["path"]))

    def cue(self, tracks: list[dict], start: int = 0, pos: float = 0.0) -> None:
        """Restore a session: the queue and the current track at ``pos``, paused (nothing is loaded until ▶)."""
        self.queue.set(tracks, start)
        self.queue_changed.emit()
        t = self.current
        if t is None:
            return
        self._loaded = []
        self._cue_pos = max(0.0, pos)
        self._cue_id = t["id"]                   # the position belongs to THIS track only
        self._pos, self._dur = self._cue_pos, float(t.get("duration") or 0)
        self._set_state("paused")
        self.track_changed.emit(t)
        self.position_changed.emit(self._pos, self._dur)

    def _start_current(self) -> None:
        t = self.current
        if t is None:
            self.stop()
            self.track_changed.emit(None)
            return
        start = getattr(self, "_cue_pos", 0.0) if getattr(self, "_cue_id", None) == t["id"] else 0.0
        self._cue_pos, self._cue_id = 0.0, None
        self.mpv.loop_file = "inf" if self.queue.repeat == "one" else "no"
        self.mpv.pause = False
        if start > 0:
            self.mpv["start"] = f"{start:.2f}"          # reset after the file has started (see _on_pos)
            self._start_reset = True
        self.mpv.command("loadfile", self._path(t), "replace")
        self._loaded = [t["id"]]
        self._pos, self._dur = start, float(t.get("duration") or 0)
        self.tracker.start(t["id"], self._dur)
        self._load_pcm(t)
        if start <= 0:
            self._append_next()
        self._set_state("playing")
        self.track_changed.emit(t)
        self.queue_changed.emit()

    def _append_next(self) -> None:
        """Make sure mpv's playlist is exactly [current, next-from-queue] (next missing at the queue end)."""
        try:
            while len(self._loaded) > 1:
                self.mpv.command("playlist-remove", 1)
                self._loaded.pop()
            nxt = self.queue.peek_next()
            if nxt is not None and self.queue.repeat != "one":
                self.mpv.command("loadfile", self._path(nxt), "append")
                self._loaded.append(nxt["id"])
        except Exception:
            log.debug("append next failed", exc_info=True)

    def play(self) -> None:
        if self.current is None:
            return
        if not self._loaded:
            self._start_current()
        else:
            self.mpv.pause = False
            self._set_state("playing")

    def pause(self) -> None:
        self.mpv.pause = True
        self._set_state("paused")

    def stop(self) -> None:
        try:
            self.mpv.command("stop")
        except Exception:
            pass
        self._loaded = []
        self._pos = 0.0
        self._set_state("stopped")

    def seek(self, seconds: float) -> None:
        self.tracker.seek()
        try:
            self.mpv.command("seek", max(0.0, seconds), "absolute")
        except Exception:
            log.debug("seek failed", exc_info=True)
        self._pos, self._pos_stamp = max(0.0, seconds), time.monotonic()

    def duration(self) -> float:
        t = self.current
        return self._dur if self._dur > 0 else float((t or {}).get("duration") or 0)

    def set_volume(self, v: float) -> None:
        self._volume = max(0.0, min(1.0, v))
        self.mpv.volume = int(self._volume * 100)

    def set_shuffle(self, mode: str) -> None:
        self.queue.set_shuffle(mode)
        self._append_next()
        self.queue_changed.emit()

    def set_repeat(self, mode: str) -> None:
        self.queue.set_repeat(mode)
        self.mpv.loop_file = "inf" if mode == "one" else "no"
        self._append_next()
        self.queue_changed.emit()

    def drop_upcoming(self) -> None:
        self.queue.truncate_after_current()
        self._append_next()
        self.queue_changed.emit()

    def enqueue(self, tracks: list[dict], next_: bool = False) -> None:
        was_empty = not self.queue.items
        self.queue.add(tracks, next_)
        self.queue_changed.emit()
        if was_empty:
            self._start_current()
        else:
            self._append_next()

    def prev(self) -> None:
        if self._pos > 3:
            self.seek(0)
            return
        self.queue.prev()
        self._start_current()

    @property
    def deck(self):  # callers only use deck.player.position()/duration() (snapshot, seek bar)
        class _D:
            class player:  # noqa: N801 - tiny shim
                position = staticmethod(lambda: int(self._pos * 1000))
                duration = staticmethod(lambda: int(self._dur * 1000))
        return _D

    # ---- mpv events (GUI thread) --------------------------------------
    def _on_pos(self, pos: float) -> None:
        if getattr(self, "_start_reset", False):
            self._start_reset = False
            try:
                self.mpv["start"] = "none"
            except Exception:
                pass
            self._append_next()
        if self.queue.repeat == "one" and self._pos - pos > 1.0 and self.current is not None:
            self.tracker.start(self.current["id"], self._dur)     # looped: count the next round again
        self._pos, self._pos_stamp = pos, time.monotonic()
        self.tracker.update(pos, playing=self._state == "playing")
        self.position_changed.emit(pos, self._dur)

    def _on_plpos(self, idx) -> None:
        """mpv started the appended next entry on its own -> advance the queue."""
        if idx != 1 or len(self._loaded) < 2:
            return
        t = self.queue.next(auto=True)
        self._loaded.pop(0)
        try:
            self.mpv.command("playlist-remove", 0)
        except Exception:
            pass
        if t is None:
            return
        self._dur = float(t.get("duration") or 0)
        self.tracker.start(t["id"], self._dur)
        self._load_pcm(t)
        self._append_next()
        self.track_changed.emit(t)
        self.queue_changed.emit()

    def _on_end_file(self, reason: str) -> None:
        if reason == "eof" and len(self._loaded) <= 1 and self._state == "playing":
            if self.queue.peek_next() is None:       # queue finished
                self._loaded = []
                self._set_state("stopped")
                self.track_changed.emit(None)

    # ---- spectrum source ------------------------------------------------
    def _load_pcm(self, t: dict) -> None:
        tid = t["id"]
        self._pcm_id, self._pcm = tid, None

        def work() -> None:
            try:
                import soundfile as sf
                with sf.SoundFile(self._path(t)) as f:
                    frames = min(int(f.frames), int(f.samplerate) * SAMPLE_MAX_SEC)
                    data = f.read(frames, dtype="float32", always_2d=True)
                    sr = int(f.samplerate)
                self._emit(self._pcm_ready, data.mean(axis=1), sr, tid)
            except Exception:
                log.debug("pcm decode for visuals failed", exc_info=True)
        threading.Thread(target=work, daemon=True, name="pcm-decode").start()

    def _set_pcm(self, pcm: np.ndarray, sr: int, tid: int) -> None:
        if tid == self._pcm_id:
            self._pcm, self._pcm_sr = pcm, sr

    def _emit_samples(self) -> None:
        if self._state != "playing" or self._pcm is None:
            return
        pos = self._pos + (time.monotonic() - self._pos_stamp)
        end = int(pos * self._pcm_sr)
        if 0 < end <= self._pcm.size:
            self.samples.emit(self._pcm[max(0, end - 2048):end].copy(), self._pcm_sr)

    def snapshot(self) -> dict:
        s = super().snapshot()
        s["engine"] = "mpv (gapless)"
        return s


def make_player(db: Database, volume: float, engine: str = "auto") -> PlayerController:
    """engine: auto | mpv | qt.  'auto' uses libmpv when its DLL is found."""
    if engine in ("auto", "mpv") and mpv_available():
        try:
            return MpvPlayerController(db, volume=volume)
        except Exception:
            log.exception("libmpv engine failed, falling back to QtMultimedia")
    p = PlayerController(db, volume=volume)
    p.engine = "qt"
    return p
