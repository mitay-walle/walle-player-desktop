"""The library server as a home-server service (python -m musicplayer.server --service) and what Linux needs."""
import json
import os
import socket
import time
import urllib.request
from pathlib import Path

import pytest

from .conftest import make_mp3


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_service_serves_scans_and_backs_up(tmp_path, library):
    from musicplayer.server.service import Service
    home = tmp_path / "home"
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({"library_root": str(library), "auto_tag": True}), encoding="utf-8")
    svc = Service(home, scan_every=30, autotag=True, host="127.0.0.1", port=free_port())
    assert svc.start()
    try:
        for _ in range(200):
            if svc.last_scan is not None and list((home / "backups").glob("library-*.db")):
                break
            time.sleep(0.1)
        assert svc.last_scan["added"] == 3 and svc.relay is None          # no VPS in remote.json: no tunnel
        assert len(list((home / "backups").glob("library-*.db"))) == 1     # the first backup right after the scan
        ping = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{svc.server.port}/api/ping", timeout=5).read())
        assert ping["app"] == "WallePlayer"
        make_mp3(library / "Новое" / "Песня.mp3", artist="Новое", title="Песня")
        assert svc.scan_once()["added"] == 1                                # the next scan picks up new files
    finally:
        svc.stop()
    assert not svc.server.running


def test_service_without_music_folder_keeps_serving(tmp_path):
    from musicplayer.server.service import Service
    svc = Service(tmp_path, host="127.0.0.1", port=free_port())
    assert svc.start()
    try:
        assert svc.scan_once() is None and svc.server.running
    finally:
        svc.stop()


def test_backup_keeps_the_newest_copies(tmp_path, library):
    from musicplayer.core.db import Database
    from musicplayer.core.scanner import scan
    from musicplayer.server.service import backup
    db = Database(tmp_path / "library.db")
    scan(db, library, tmp_path / "covers")
    db.close()
    folder = tmp_path / "backups"
    folder.mkdir()
    for i in range(10):
        (folder / f"library-2026010{i}-0000.db").write_bytes(b"x")
        os.utime(folder / f"library-2026010{i}-0000.db", (1, 1))
    assert backup(tmp_path, tmp_path / "library.db", keep=8) is not None
    names = sorted(p.name for p in folder.glob("library-*.db"))
    assert len(names) == 8 and names[0] == "library-20260103-0000.db"
    assert backup(tmp_path, tmp_path / "library.db", keep=8) is None        # fresh enough: no second copy


def test_moved_from_windows_to_linux_paths_are_the_same_tracks():
    """Tracks catalogued on Windows (D:\\...) are recognised in a Linux folder by artist / album / file."""
    from musicplayer.core.scanner import _tail
    win = r"D:\Music\mp3\Кино\Группа крови\01 - Группа крови.mp3"
    assert _tail(win) == ("кино", "группа крови", "01 - группа крови.mp3")
    assert _tail(Path("/mnt/media/Music/mp3/Кино/Группа крови/01 - Группа крови.mp3")) == _tail(win)
    assert _tail("/mnt/media/Music/mp3/Кино/01.mp3") == ("mp3", "кино", "01.mp3")


@pytest.mark.skipif(os.name == "nt", reason="file modes are a Linux / macOS thing")
def test_relay_key_gets_mode_600(tmp_path):
    from musicplayer.server.relay import protect_key
    key = tmp_path / "relay_key"
    key.write_text("k")
    key.chmod(0o644)
    protect_key(key)
    assert key.stat().st_mode & 0o777 == 0o600
