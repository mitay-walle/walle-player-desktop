"""Application context shared by every page, plus persistent settings."""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from musicplayer.core.db import Database
from musicplayer.core.paths import data_dir
from musicplayer.ui.jobs import JobManager
from musicplayer.ui.mpv_player import make_player
from musicplayer.ui.player import PlayerController

log = logging.getLogger(__name__)
DEFAULT_ROOT = str(Path.home() / "Music")      # the first scan asks; the chosen folder is remembered


class AppEvents(QObject):
    """Cross-page signals."""

    library_changed = Signal()          # tracks added/removed (scan)
    tracks_changed = Signal(list)       # ids whose genres/moods/plays changed
    tracks_removed = Signal(list)       # ids sent to the Recycle Bin: lists drop those rows in place (no reload)
    hidden_changed = Signal()           # «Не показывать» / «Вернуть» (core/hidden.py)
    listener_changed = Signal()         # likes and «Не показывать» now act for another listener
    delete_marks_changed = Signal()     # «Пометить на удаление» list changed (core/deletemarks.py)
    navigate = Signal(str)
    show_track = Signal(int)
    favorites_changed = Signal()        # a track / album / artist was hearted or un-hearted
    show_rows = Signal(list, str)       # tracks, title -> shown in the Library page
    request_scan = Signal()             # ask the main window to pick the music folder and scan it
    request_autotag = Signal()          # tag tracks without a label with the trained model now


@dataclass
class Settings:
    library_root: str = DEFAULT_ROOT
    volume: float = 0.7
    mcp_port: int = 8766
    ui_zoom: float = 1.0
    tile_size: int = 160
    color_strip: bool = True
    engine: str = "auto"     # auto | mpv | qt
    last_queue: list | None = None     # track ids of the play queue at exit
    last_index: int = 0
    last_pos: float = 0.0
    wave: dict | None = None          # «Моя волна» at exit: {active, moods, character, meta: {track id: [kind, moods, conf]}}
    clap_hf_home: str = ""             # folder with the CLAP weights chosen by the user (a HuggingFace home)
    window_geom: str = ""             # QMainWindow.saveGeometry() as hex: size, position, maximised
    mini_pos: list | None = None      # last position of the mini player window
    listener: str = "owner"           # whose likes / hidden things the app shows and sets (a listener's id)
    auto_tag: bool = True            # tag new tracks with the trained model after every scan
    auto_mode: str = "balanced"      # strict | balanced | wide: how readily the model assigns moods by itself (source 'auto')


class AppContext:
    def __init__(self, home: Path | None = None) -> None:
        self.home = Path(home) if home else data_dir()
        self.home.mkdir(parents=True, exist_ok=True)
        self.db_path = self.home / "library.db"
        self.covers = self.home / "covers"
        self.models = self.home / "models"
        self.settings_file = self.home / "settings.json"
        self.settings = self._load_settings()
        self.db_notice = ""
        if self.settings.clap_hf_home and not os.environ.get("MUSICPLAYER_HF_HOME"):
            os.environ["MUSICPLAYER_HF_HOME"] = self.settings.clap_hf_home
        self.db = self._open_db()
        self._backup()
        self.events = AppEvents()
        self.jobs = JobManager()
        self.player: PlayerController = make_player(self.db, self.settings.volume,
                                                    os.environ.get("MUSICPLAYER_ENGINE") or self.settings.engine)
        self.closed = False
        self.listener_name = ""                       # shown in tooltips when a guest is chosen (set by the window)
        self.label_clipboard: dict | None = None    # «Скопировать метки» (see core/labelclip.py)

    def _open_db(self) -> Database:
        """Open the catalogue; a corrupt file is moved aside and the newest backup (if any) restored."""
        try:
            db = Database(self.db_path)
            if db.quick_check():
                return db
            db.close()
        except Exception:
            log.exception("catalogue cannot be opened")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        bad = self.db_path.with_name(f"library.corrupt-{stamp}.db")
        for suffix in ("", "-wal", "-shm"):
            f = Path(str(self.db_path) + suffix)
            if f.exists():
                f.replace(Path(str(bad) + suffix))
        backups = sorted((self.home / "backups").glob("library-*.db"))
        if backups:
            shutil.copy2(backups[-1], self.db_path)
            self.db_notice = f"База была повреждена ({bad.name}); восстановлена из копии {backups[-1].name}."
        else:
            self.db_notice = f"База была повреждена и сохранена как {bad.name}; создан новый каталог — просканируйте библиотеку."
        log.warning(self.db_notice)
        return Database(self.db_path)

    def _backup(self, keep: int = 8) -> None:
        """A consistent copy in backups/ at start-up when the newest is over 2 hours old (the last ``keep`` are kept);
        skipped for an empty catalogue."""
        try:
            if self.db.conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 0:
                return
            folder = self.home / "backups"
            folder.mkdir(exist_ok=True)
            newest = max((f.stat().st_mtime for f in folder.glob("library-*.db")), default=0)
            if time.time() - newest > 2 * 3600:
                self.db.backup(folder / f"library-{time.strftime('%Y%m%d-%H%M')}.db")
            for old in sorted(folder.glob("library-*.db"))[:-keep]:
                old.unlink(missing_ok=True)
        except Exception:
            log.exception("backup failed")

    def _load_settings(self) -> Settings:
        try:
            raw = json.loads(self.settings_file.read_text(encoding="utf-8"))
            return Settings(**{k: v for k, v in raw.items() if k in Settings.__dataclass_fields__})
        except (OSError, ValueError):
            return Settings()

    def save_settings(self) -> None:
        self.settings.volume = self.player.volume
        self.settings_file.write_text(json.dumps(self.settings.__dict__, ensure_ascii=False, indent=1),
                                      encoding="utf-8")

    def open_db(self) -> Database:
        """A separate connection for worker threads (WAL allows a concurrent writer)."""
        return Database(self.db_path)

    # ---- the listener the app acts for (likes, «Не показывать») ---------------------------------------------
    @property
    def listener(self) -> str:
        return self.settings.listener or "owner"

    def set_listener(self, uid: str, name: str = "") -> None:
        self.listener_name = "" if uid == "owner" else name
        if uid != self.listener:
            self.settings.listener = uid
            self.save_settings()
            self.events.listener_changed.emit()
            self.events.favorites_changed.emit()
            self.events.hidden_changed.emit()

    def is_liked(self, kind: str, key: str) -> bool:
        from musicplayer.core import likes
        return likes.is_liked(self.db, self.listener, kind, key)

    def set_liked(self, kind: str, key: str, on: bool) -> None:
        from musicplayer.core import likes
        likes.set_liked(self.db, self.listener, kind, key, on)

    def liked(self, kind: str) -> list[str]:
        from musicplayer.core import likes
        return likes.keys(self.db, self.listener, kind)

    def close(self) -> None:
        self.closed = True
        self.jobs.shutdown()
        self.player.shutdown()
        self.save_settings()
        self.db.close()
