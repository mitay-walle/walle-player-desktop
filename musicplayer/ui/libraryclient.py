"""The desktop app working with a library that lives on a home server (``python -m musicplayer.server --service``).

The app keeps its own copy of the catalogue and syncs it both ways with the server (core/sync.py): likes, labels and
plays made here reach phones, theirs come here; the server's scans bring new tracks.  The music folder here
(``settings.library_root``) is kept a full mirror of the server's: new files are downloaded after the sync (all
audio analysis runs on this computer); until then they play straight from the server.  Deleting a
track deletes it on the server.  The server is found by the library id (remote.json): at home by the UDP discovery,
elsewhere through the internet address of remote.json (the VPS).
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath, PureWindowsPath

from PySide6.QtCore import QObject, QTimer, Signal

from musicplayer.core import remotefile, sync
from musicplayer.core.db import Database
from musicplayer.server import config
from musicplayer.server.runner import DISCOVER, lan_addresses

log = logging.getLogger(__name__)
EVERY_MS = 30_000
MIRROR_EVERY = 600              # s: how often the copy of the music folder is checked against the catalogue
BATCH = 1000


def discover(library_id: str, port: int = config.DEFAULT_PORT, wait: float = 1.5) -> list[str]:
    """Base URLs of servers on the home network that serve this library (UDP broadcast)."""
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.settimeout(0.3)
            # 255.255.255.255 leaves through one adapter only (on Windows often a virtual one: WSL, VPN), so also
            # the broadcast address of every home network this computer is on
            targets = ["255.255.255.255"] + [ip.rsplit(".", 1)[0] + ".255" for ip in lan_addresses()]
            for t in dict.fromkeys(targets):
                try:
                    s.sendto(DISCOVER, (t, port))
                except OSError:
                    pass
            end = time.time() + wait
            while time.time() < end:
                try:
                    data, addr = s.recvfrom(1024)
                except socket.timeout:
                    continue
                try:
                    info = json.loads(data)
                except ValueError:
                    continue
                if info.get("library") == library_id and addr[0] not in ("127.0.0.1",):
                    found.append(f"http://{addr[0]}:{int(info.get('port') or port)}")
    except OSError as e:
        log.debug("discovery failed: %s", e)
    return list(dict.fromkeys(found))


def public_url(cfg) -> str | None:
    """The server's internet address from remote.json (the VPS route)."""
    if cfg.net_mode == "relay" and cfg.relay_https.strip():
        return f"https://{cfg.relay_https.strip()}"
    if cfg.net_mode == "relay" and cfg.relay_host.strip():
        return f"http://{cfg.relay_host.strip()}:{cfg.relay_port}"
    if cfg.public_url:
        return cfg.public_url.rstrip("/")
    return None


def share_link(ctx, path: str) -> str | None:
    """A link to ``track/<id>``, ``artist/<name>`` or ``album/<artist>/<album>`` in the web player, without a key: it
    opens only for someone who already listens to the library on their device (their cookie or the app's key)."""
    base = public_url(config.load(ctx.home))
    if not base:
        s = (ctx.settings.library_server or "").split()
        base = s[0].rstrip("/") if s and s[0] != "auto" else None
    return f"{base}/open/{path}" if base else None   # the server sends it on: the app on Android, else the web page


def artist_path(name: str) -> str:
    from urllib.parse import quote
    return f"artist/{quote(name, safe='')}"


def album_path(artist: str, album: str) -> str:
    from urllib.parse import quote
    return f"album/{quote(artist, safe='')}/{quote(album, safe='')}"


def copy_share(ctx, parent, path: str) -> None:
    """«Поделиться»: the link goes to the clipboard (paste it into a messenger); a note when there is no address."""
    from PySide6.QtGui import QCursor
    from PySide6.QtWidgets import QApplication, QMessageBox, QToolTip
    url = share_link(ctx, path)
    if not url:
        QMessageBox.information(parent, "Поделиться", "У библиотеки нет адреса в интернете — откройте «Другие "
                                                      "устройства» и настройте доступ через интернет.")
        return
    QApplication.clipboard().setText(url)
    QToolTip.showText(QCursor.pos(), "Ссылка скопирована — вставьте её в мессенджер.\n"
                                     "Откроется у тех, кто уже слушает эту библиотеку", parent)


def share_menu(ctx, button, t: dict | None) -> None:
    """The «Поделиться» button next to a track (player bar): the track, its album or its artist."""
    from PySide6.QtWidgets import QMenu
    if not t:
        return
    artist = t.get("albumartist") or t.get("artist") or ""
    m = QMenu(button)
    m.addAction("Трек", lambda: copy_share(ctx, button, f"track/{t['id']}"))
    if t.get("album"):
        m.addAction("Альбом", lambda: copy_share(ctx, button, album_path(artist, t["album"])))
    if artist:
        m.addAction("Исполнителя", lambda: copy_share(ctx, button, artist_path(artist)))
    m.exec(button.mapToGlobal(button.rect().bottomLeft()))


class LibraryClient(QObject):
    synced = Signal(dict)            # (main thread) {"pushed", "pulled", "new", "mirrored", "tracks", "error"}
    changed = Signal()               # the state shown on the «Сервер библиотеки» button changed

    def __init__(self, ctx) -> None:
        super().__init__()
        self.ctx = ctx
        self.cfg = config.load(ctx.home)
        self.base: str | None = None             # the server in use
        self._base_at = 0.0                      # when it was chosen
        self.error: str | None = None
        self.last_sync = 0.0
        self.last_result: dict = {}
        self.mirror = {"missing": None, "copied": 0, "failed": 0, "error": None, "checked": 0.0}
        self._busy = threading.Lock()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.sync_now)
        self.synced.connect(self._after_sync)

    # ---- state ----------------------------------------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self.ctx.settings.library_server) and bool(self.token)

    @property
    def token(self) -> str:
        return self.cfg.owner.get("token", "") if self.cfg.owner else ""

    @property
    def paused(self) -> bool:
        return bool(self.ctx.settings.sync_paused)

    def set_paused(self, on: bool) -> None:
        """Pause everything that uses the network: the sync and the downloads (a file being fetched stops at once)."""
        self.ctx.settings.sync_paused = bool(on)
        self.ctx.save_settings()
        self.changed.emit()
        if not on:
            self.sync_now()

    def set_limit(self, kbps: int) -> None:
        self.ctx.settings.sync_limit_kbps = max(0, int(kbps))
        self.ctx.save_settings()
        self.changed.emit()

    def set_mirror(self, on: bool) -> None:
        self.ctx.settings.sync_mirror = bool(on)
        self.ctx.save_settings()
        if on:
            self.mirror["checked"] = 0.0
            self.sync_now()
        self.changed.emit()

    def status(self) -> dict:
        return {"enabled": self.enabled, "paused": self.paused, "limit_kbps": self.ctx.settings.sync_limit_kbps,
                "mirror_on": self.ctx.settings.sync_mirror, "busy": self._busy.locked(), "server": self.base, "setting": self.ctx.settings.library_server,
                "server_root": self.ctx.settings.server_root, "last_sync": self.last_sync, "error": self.error,
                "last": self.last_result, "mirror": self.mirror,
                "node": sync.meta(self.ctx.db.conn, "node") if sync.enabled(self.ctx.db.conn) else None}

    def start(self) -> None:
        if not self.enabled:
            return
        sync.enable(self.ctx.db.conn)
        self._timer.start(EVERY_MS)
        QTimer.singleShot(1500, self.sync_now)

    def connect_to(self, server: str = "auto", server_root: str = "") -> dict:
        """Work with the library on a server: ``auto`` (find it at home or via the VPS) or a base URL."""
        s = self.ctx.settings
        s.library_server, s.server_root = server or "auto", server_root or s.server_root
        self.ctx.save_settings()
        self.base, self.error = None, None
        self.start()
        return self.status()

    def disconnect(self) -> dict:
        self._timer.stop()
        os.environ.pop(remotefile.ENV_SERVER, None)
        os.environ.pop(remotefile.ENV_KEY, None)
        self.ctx.settings.library_server = ""
        self.ctx.save_settings()
        self.base = None
        return self.status()

    # ---- talking to the server --------------------------------------------------------------------------------------
    def candidates(self) -> list[str]:
        s = (self.ctx.settings.library_server or "").strip()
        if s and s != "auto":
            return [u.rstrip("/") for u in s.split()]
        out = discover(self.cfg.library_id, self.cfg.port)
        pub = public_url(self.cfg)
        return out + ([pub] if pub else [])

    def _request(self, base: str, path: str, body: dict | None = None, timeout: float = 30) -> dict:
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")

    def _pick(self) -> str:
        public = public_url(self.cfg)
        if self.base and not (self.base == public and time.time() - self._base_at > 300):
            return self.base                          # (through the VPS: look for it at home again now and then)
        errors = []
        for base in self.candidates():
            try:
                info = self._request(base, "/api/ping", timeout=4)
            except (OSError, ValueError) as e:
                errors.append(f"{base}: {e}")
                continue
            if info.get("library") == self.cfg.library_id and info.get("authorized"):
                self.base, self._base_at = base, time.time()
                os.environ[remotefile.ENV_SERVER], os.environ[remotefile.ENV_KEY] = base, self.token   # analysis jobs
                return base
            errors.append(f"{base}: another library or the key is not accepted")
        raise ConnectionError("сервер библиотеки не найден" + (": " + "; ".join(errors) if errors else ""))

    def request(self, path: str, body: dict | None = None, timeout: float = 30) -> dict:
        try:
            return self._request(self._pick(), path, body, timeout)
        except (OSError, ValueError):
            self.base = None                              # next time: look for the server again
            raise

    def stream_url(self, tid: int) -> str | None:
        if not self.enabled:
            return None
        try:
            base = self._pick()
        except ConnectionError:
            return None
        return f"{base}/api/stream/{int(tid)}?k={urllib.parse.quote(self.token)}"

    def trash(self, tid: int) -> bool:
        """Delete the track's file on the server (into its trash folder)."""
        try:
            return bool(self.request(f"/api/tracks/{int(tid)}/trash", {}).get("ok"))
        except (OSError, ValueError) as e:
            log.warning("trash on the server failed: %s", e)
            return False

    # ---- sync -------------------------------------------------------------------------------------------------------
    def sync_now(self) -> None:
        if not self.enabled or self.paused or not self._busy.acquire(blocking=False):
            return
        self.changed.emit()
        threading.Thread(target=self._run, name="walle-sync", daemon=True).start()

    def _run(self) -> None:
        res = {"pushed": 0, "pulled": 0, "new": 0, "mirrored": 0, "tracks": [], "error": None}
        db = None
        try:
            db = Database(self.ctx.db_path)
            sync.enable(db.conn)
            me = sync.node(db.conn)
            while True:                                   # my changes first
                out, last = sync.entries_since(db.conn, int(sync.meta(db.conn, "pushed", "0")), BATCH, origin=me)
                if out:
                    self.request("/api/sync/push", {"node": me, "entries": out}, timeout=120)
                    res["pushed"] += len(out)
                if last == int(sync.meta(db.conn, "pushed", "0")):
                    break
                sync.set_meta(db.conn, "pushed", last)
            while True:                                   # then everyone else's
                since = int(sync.meta(db.conn, "pulled", "0"))
                r = self.request(f"/api/sync/pull?since={since}&node={me}&limit={BATCH}", timeout=120)
                got = sync.apply(db, r.get("entries") or [], map_path=self._map_path)
                res["pulled"] += got["applied"]
                res["new"] += got["new"]
                res["tracks"] += got["tracks"]
                sync.set_meta(db.conn, "pulled", r.get("last", since))
                if int(r.get("last", since)) >= int(r.get("head", 0)) or int(r.get("last", since)) == since:
                    break
            self._fetch_covers(db, res["tracks"])
            if self.ctx.settings.sync_mirror and not self.paused and (
                    res["new"] or time.time() - self.mirror["checked"] > MIRROR_EVERY):
                res["mirrored"] = self._mirror(db)              # before the analysis: it then reads local files
        except Exception as e:  # noqa: BLE001 - a dropped network must not break the app; shown in the status
            res["error"] = str(e)
            log.warning("sync failed: %s", e)
        finally:
            if db is not None:
                db.close()
            self._busy.release()
        self.synced.emit(res)

    def _map_path(self, server_path: str) -> str:
        """The server's path of a new track -> the same file in this computer's copy of the music folder."""
        root = (self.ctx.settings.server_root or "").rstrip("/\\")
        local = self.ctx.settings.library_root
        if root and local and server_path.startswith(root):
            rel = server_path[len(root):].lstrip("/\\")
            parts = (PureWindowsPath(rel) if "\\" in rel else PurePosixPath(rel)).parts
            return str(Path(local).joinpath(*parts))
        return server_path

    def _mirror(self, db, limit: int = 300) -> int:
        """Keep a full copy of the music here: files the server has and this computer lacks are downloaded into the
        same folders of library_root.  Only new files - nothing is overwritten or deleted (a server whose disk is
        unplugged reports every track missing; that must never empty this copy)."""
        root = os.path.normcase(os.path.abspath(self.ctx.settings.library_root or ""))
        if not self.ctx.settings.server_root or not root or not os.path.isdir(root):
            return 0
        todo = [(r[0], r[1]) for r in db.conn.execute("SELECT id, path FROM tracks WHERE missing=0")
                if os.path.normcase(os.path.abspath(r[1])).startswith(root + os.sep) and "#" not in r[1]
                and not os.path.exists(r[1])]
        self.mirror.update(missing=len(todo), checked=time.time(), error=None)
        copied = 0
        for tid, path in todo[:limit]:
            part = path + ".part"
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                req = urllib.request.Request(f"{self._pick()}/api/stream/{int(tid)}",
                                             headers={"Authorization": f"Bearer {self.token}"})
                stop = lambda: self.paused or self.ctx.closed or not self.ctx.settings.sync_mirror  # noqa: E731
                with remotefile.download_slot(stop), urllib.request.urlopen(req, timeout=300) as r,                         open(part, "wb") as f:
                    remotefile.copy_limited(r, f, stop=stop)
                if os.path.exists(path):                    # appeared meanwhile: keep that one
                    os.remove(part)
                    continue
                os.replace(part, path)
                st = os.stat(path)
                db.conn.execute("UPDATE tracks SET mtime=?, size=? WHERE id=?", (st.st_mtime, st.st_size, tid))
                copied += 1
                self.mirror["missing"] = len(todo) - copied
                self.changed.emit()
            except remotefile.Paused:
                try:
                    os.remove(part)
                except OSError:
                    pass
                break
            except (OSError, ValueError) as e:
                self.mirror["failed"] += 1
                self.mirror["error"] = f"{os.path.basename(path)}: {e}"
                try:
                    os.remove(part)
                except OSError:
                    pass
        self.mirror["copied"] += copied
        self.mirror["missing"] = len(todo) - copied
        return copied

    def _fetch_covers(self, db, tids: list[int]) -> None:
        if not tids:
            return
        self.ctx.covers.mkdir(parents=True, exist_ok=True)
        keys = {r[0] for r in db.conn.execute(
            f"SELECT DISTINCT cover_key FROM tracks WHERE cover_key IS NOT NULL AND id IN ({','.join('?' * len(tids))})",
            tids)}
        for key in keys:
            out = self.ctx.covers / f"{key}.jpg"
            if out.exists():
                continue
            try:
                req = urllib.request.Request(f"{self._pick()}/api/cover/{urllib.parse.quote(key)}",
                                             headers={"Authorization": f"Bearer {self.token}"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    out.write_bytes(r.read())
            except (OSError, ValueError):
                continue

    def _after_sync(self, res: dict) -> None:
        self.last_sync, self.error, self.last_result = time.time(), res.get("error"), res
        if res.get("pulled"):
            ev = self.ctx.events
            for sig in ("library_changed", "favorites_changed", "hidden_changed", "delete_marks_changed"):
                s = getattr(ev, sig, None)
                if s is not None:
                    s.emit()


def media_source(client: LibraryClient | None, track: dict) -> str:
    """What the player opens: the local file when it is here, else the server's stream (when connected)."""
    p = track.get("path") or ""
    if p and os.path.exists(p):
        return p
    if client is not None and client.enabled:
        url = client.stream_url(track["id"])
        if url:
            return url
    return p
