"""The library server as a long-running service (a home server / mini PC without the desktop window; systemd on Linux).

    python -m musicplayer.server --service [--scan-every 30] [--autotag]

Does what the desktop app does for other devices while it runs, without Qt:
- serves the library (port, listeners and keys from remote.json, the same data folder as the app);
- keeps the internet route of remote.json up: the reverse tunnel to the user's VPS (``net_mode`` = relay);
- scans the music folder (``library_root`` of settings.json) at start and every ``scan_every`` minutes: new files are
  added, moved ones keep their labels (scanner.relocate_moved); with ``--autotag`` the mood model also tags new tracks
  (audio analysis: heavy for a small home server, so off by default - the desktop app does it on a strong computer);
- keeps a consistent copy of the catalogue in backups/ once a day (the newest 8 are kept);
- journals every change for desktop copies of the catalogue (core/sync.py, /api/sync/pull and /api/sync/push).
SIGTERM / SIGINT stop it cleanly.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from musicplayer.core.db import Database
from musicplayer.server import config
from musicplayer.server.relay import Relay
from musicplayer.server.runner import RemoteServer

log = logging.getLogger("walle.service")
BACKUP_EVERY = 24 * 3600
BACKUPS_KEPT = 8


def read_settings(home: Path) -> dict:
    """The desktop app's settings.json (only plain values are used here: library_root, auto_tag, auto_mode)."""
    try:
        return json.loads((home / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def backup(home: Path, db_path: Path, every: float = BACKUP_EVERY, keep: int = BACKUPS_KEPT) -> Path | None:
    """A consistent copy in backups/ when the newest one is older than ``every`` seconds; the last ``keep`` stay."""
    folder = home / "backups"
    folder.mkdir(exist_ok=True)
    newest = max((f.stat().st_mtime for f in folder.glob("library-*.db")), default=0)
    out = None
    if time.time() - newest > every:
        db = Database(db_path)
        try:
            if db.conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]:
                out = folder / f"library-{time.strftime('%Y%m%d-%H%M')}.db"
                db.backup(out)
                log.info("backup %s", out.name)
        finally:
            db.close()
    for old in sorted(folder.glob("library-*.db"))[:-keep]:
        old.unlink(missing_ok=True)
    return out


class Service:
    def __init__(self, home: Path, scan_every: float = 30, autotag: bool = False, host: str = "0.0.0.0",
                 port: int | None = None) -> None:
        self.home = Path(home)
        self.db_path = self.home / "library.db"
        self.covers = self.home / "covers"
        self.scan_every = max(1.0, float(scan_every)) * 60
        self.autotag = autotag
        self.cfg = config.load(self.home)
        self.server = RemoteServer(self.db_path, self.covers, self.cfg.users, port or self.cfg.port, host,
                                   on_users=lambda: config.save(self.home, self.cfg), library_id=self.cfg.library_id)
        self.relay: Relay | None = None
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self.last_scan: dict | None = None

    # ---- lifecycle ----------------------------------------------------
    def start(self) -> bool:
        from musicplayer.core import sync
        db = Database(self.db_path)                      # creates / migrates the catalogue before anyone reads it
        try:
            log.info("sync journal on (node %s): desktop copies can sync", sync.enable(db.conn))
        finally:
            db.close()
        if not self.server.start():
            log.error("server not started: %s", self.server.error)
            return False
        log.info("library server on port %s: %s", self.server.port, ", ".join(self.server.urls()))
        self._start_net()
        self._worker = threading.Thread(target=self._loop, name="walle-service", daemon=True)
        self._worker.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self.relay is not None:
            self.relay.stop()
        if self._worker is not None:
            self._worker.join(10)
        self.server.stop()
        log.info("stopped")

    def wait(self) -> None:
        while not self._stop.wait(0.5):
            pass

    # ---- internet route -----------------------------------------------
    def _start_net(self) -> None:
        c = self.cfg
        if c.net_mode == "manual" and c.public_url.strip():   # an address of its own (router, DNS): links use it
            self.server.access.addresses = lambda: {"lan": self.server.urls(), "internet": [c.public_url.strip()]}
        if c.net_mode != "relay" or not c.relay_host.strip():
            log.info("internet route: %s (not started by the service)", c.net_mode)
            return
        self.server.access.loopback_key = "через VPS"
        public = f"https://{c.relay_https.strip()}" if c.relay_https.strip() else f"http://{c.relay_host.strip()}:{c.relay_port}"
        self.server.access.addresses = lambda: {"lan": self.server.urls(), "internet": [public]}   # links that work away
        self.relay = Relay(self.home, c.relay_host, c.relay_user, self.server.port, c.relay_port, c.relay_ssh_port,
                           on_change=self._relay_changed,
                           bind="127.0.0.1" if c.relay_https.strip() else "0.0.0.0")
        self.relay.start()

    def _relay_changed(self) -> None:
        r = self.relay
        if r is not None:
            log.info("VPS tunnel: %s%s", r.state, f" ({r.error})" if r.error else "")

    # ---- periodic work ------------------------------------------------
    def _loop(self) -> None:
        next_scan = 0.0
        while not self._stop.is_set():
            if time.time() >= next_scan:
                try:
                    self.scan_once()
                except Exception:  # noqa: BLE001
                    log.exception("scan failed")
                next_scan = time.time() + self.scan_every
            try:
                backup(self.home, self.db_path)              # after the first scan: a fresh catalogue gets one too
            except Exception:  # noqa: BLE001
                log.exception("backup failed")
            self._stop.wait(min(60.0, max(1.0, next_scan - time.time())))

    def scan_once(self) -> dict | None:
        from musicplayer.core import scanner
        settings = read_settings(self.home)
        root = settings.get("library_root") or ""
        if not root or not Path(root).is_dir():
            log.warning("music folder not found: %r (settings.json library_root)", root)
            return None
        db = Database(self.db_path)
        try:
            r = scanner.scan(db, root, str(self.covers), lambda *a: self._check())
        finally:
            db.close()
        self.last_scan = r
        if r["added"] or r["updated"] or r.get("moved") or r["missing"]:
            log.info("scan %s: new %s, updated %s, moved %s, missing %s, errors %s, total %s", root, r["added"],
                     r["updated"], r.get("moved", 0), r["missing"], len(r["errors"]), r["total"])
        if r["added"] and self.autotag and settings.get("auto_tag", True):
            self._autotag(settings.get("auto_mode", "balanced"))
        return r

    def _autotag(self, mode: str) -> None:
        from musicplayer.core import autotag, mood
        models = self.home / "models"
        if mood.load_model(models) is None:
            return
        db = Database(self.db_path)
        try:
            if autotag.pending_ids(db):
                r = autotag.tag_new_tracks(db, models, None, None, mode, workers=2)
                db.conn.commit()
                log.info("mood of new tracks: %s", r)
        except Exception:  # noqa: BLE001 - e.g. a model that needs CLAP (torch) on a server without it
            log.exception("autotag failed")
        finally:
            db.close()

    def _check(self) -> None:
        if self._stop.is_set():
            raise InterruptedError("service stopping")
