"""Audio of tracks this computer does not have: fetched from the library server for analysis, then deleted.

The desktop app connected to a home server (ui/libraryclient.py) puts the server's address and the owner's key into
the environment; analysis jobs (separate processes) inherit them.  The heavy work (descriptors, CLAP) runs here on
the strong computer; the server only hands out the file and gets the results back through sync.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

ENV_SERVER, ENV_KEY = "WALLE_LIBRARY_SERVER", "WALLE_LIBRARY_KEY"


class Paused(RuntimeError):
    """Downloads are paused by the user (the home network is needed for something else)."""


def available() -> bool:
    return bool(os.environ.get(ENV_SERVER) and os.environ.get(ENV_KEY))


def controls() -> tuple[bool, int]:
    """(paused, limit in KB/s, 0 = none) - read from the app's settings.json each time, so a running job follows the
    user's «Пауза» / speed limit at once."""
    from .paths import data_dir
    try:
        s = json.loads((data_dir() / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, 0
    return bool(s.get("sync_paused")), int(s.get("sync_limit_kbps") or 0)


def copy_limited(src, dst, stop=None, chunk: int = 64 << 10) -> int:
    """Copy a response into a file at no more than the user's speed limit; ``stop()`` -> True aborts (Paused).
    The limit and the pause are looked up again every ~2 s."""
    t0, sent, checked = time.monotonic(), 0, 0.0
    paused, limit = controls()
    while True:
        now = time.monotonic()
        if now - checked > 2:
            paused, limit = controls()
            checked = now
        if paused or (stop is not None and stop()):
            raise Paused("загрузка на паузе")
        buf = src.read(chunk)
        if not buf:
            return sent
        dst.write(buf)
        sent += len(buf)
        if limit > 0:
            ahead = sent / (limit * 1024) - (time.monotonic() - t0)
            if ahead > 0:
                time.sleep(min(ahead, 1.0))


def cache_dir() -> Path:
    from .paths import data_dir
    d = data_dir() / "audio_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextmanager
def download_slot(stop=None):
    """One download from the server at a time across the app and its analysis processes, so the speed limit is the
    total.  A lock left by a crashed process is taken over after 10 minutes."""
    lock = cache_dir() / ".download.lock"
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 600:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if controls()[0] or (stop is not None and stop()):
                raise Paused("загрузка на паузе")
            time.sleep(0.5)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def fetch(tid: int, path: str, timeout: float = 120) -> str:
    """Download track ``tid`` from the server into the cache; returns the local file (same extension as ``path``)."""
    base, key = os.environ[ENV_SERVER].rstrip("/"), os.environ[ENV_KEY]
    out = cache_dir() / f"{int(tid)}{Path(path).suffix.lower() or '.mp3'}"
    part = out.with_name(out.name + ".part")
    req = urllib.request.Request(f"{base}/api/stream/{int(tid)}?k={urllib.parse.quote(key)}",
                                 headers={"Authorization": f"Bearer {key}"})
    if controls()[0]:
        raise Paused("загрузка на паузе")
    try:
        with download_slot(), urllib.request.urlopen(req, timeout=timeout) as r, open(part, "wb") as f:
            copy_limited(r, f)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    part.replace(out)
    return str(out)


@contextmanager
def local_copy(tid: int, path: str):
    """The track's file: its own path when it is here, else a fresh download (deleted afterwards)."""
    if not path or os.path.exists(path) or not available():
        yield path
        return
    local = fetch(tid, path)
    try:
        yield local
    finally:
        Path(local).unlink(missing_ok=True)
