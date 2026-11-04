"""Artist pictures, like album covers: a photo from Deezer's open catalogue (no key, only the artist's name is sent),
kept in <covers>/artists/<key>.jpg; until it is there (or when Deezer has none) - the cover of the artist's biggest
album.  Fetching is one queue in a background thread, so a page of tiles never waits for the network.  Qt-free."""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

API = "https://api.deezer.com/search/artist?limit=10&q="
MISS_DAYS = 30                       # «no photo» is asked again after a month (Deezer may get one)
PAUSE = 0.15                         # between requests: Deezer allows 50 per 5 s


def key(name: str) -> str:
    return hashlib.sha1(name.strip().casefold().encode("utf-8")).hexdigest()[:24]


def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", "", s.casefold().replace("ё", "е"))


def folder(covers: str | Path) -> Path:
    return Path(covers) / "artists"


def cached(covers: str | Path, name: str) -> Path | None:
    f = folder(covers) / f"{key(name)}.jpg"
    return f if f.is_file() else None


def _missed(covers: str | Path, name: str) -> bool:
    f = folder(covers) / f"{key(name)}.none"
    return f.is_file() and time.time() - f.stat().st_mtime < MISS_DAYS * 86400


def find(name: str, timeout: float = 10) -> str | None:
    """The URL of the artist's photo: the same name (case, punctuation aside), the most popular such artist."""
    req = urllib.request.Request(API + urllib.parse.quote(name), headers={"User-Agent": "WallePlayer"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r).get("data") or []
    want = _norm(name)
    same = [a for a in data if _norm(a.get("name") or "") == want]
    for a in sorted(same, key=lambda a: -(a.get("nb_fan") or 0)):
        url = a.get("picture_xl") or a.get("picture_big")
        if url and "/artist//" not in url:            # Deezer's grey placeholder has no picture id
            return url
    return None


def fetch(covers: str | Path, name: str, timeout: float = 10) -> Path | None:
    """Looks the photo up and keeps it; None when there is none (remembered for a month) or the network failed."""
    d = folder(covers)
    d.mkdir(parents=True, exist_ok=True)
    k = key(name)
    try:
        url = find(name, timeout)
        if not url:
            (d / f"{k}.none").write_bytes(b"")
            return None
        req = urllib.request.Request(url, headers={"User-Agent": "WallePlayer"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
    except Exception as e:  # noqa: BLE001 - offline, Deezer unreachable: ask again next time
        log.info("artist photo for %r: %s", name, e)
        return None
    if not data.startswith(b"\xff\xd8"):
        (d / f"{k}.none").write_bytes(b"")
        return None
    tmp = d / f"{k}.part"
    tmp.write_bytes(data)
    tmp.replace(d / f"{k}.jpg")
    return d / f"{k}.jpg"


class Fetcher:
    """One background thread fetching missing photos in turn; ``on_done(name, path)`` from that thread."""

    def __init__(self, covers: str | Path, on_done: Callable[[str, Path], None] | None = None) -> None:
        self.covers, self.on_done = Path(covers), on_done
        self._q: queue.Queue[str] = queue.Queue()
        self._asked: set[str] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def want(self, name: str) -> None:
        """Queues the artist unless the photo is here, known missing or already queued."""
        if not name or cached(self.covers, name) or _missed(self.covers, name):
            return
        with self._lock:
            k = key(name)
            if k in self._asked:
                return
            self._asked.add(k)
            self._q.put(name)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="artist-photos", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                name = self._q.get(timeout=30)
            except queue.Empty:
                return
            f = fetch(self.covers, name)
            if f is None and not _missed(self.covers, name):
                with self._lock:                   # a network failure: may be asked again later
                    self._asked.discard(key(name))
            if f is not None and self.on_done:
                try:
                    self.on_done(name, f)
                except Exception:  # noqa: BLE001
                    log.exception("artist photo callback")
            time.sleep(PAUSE)
