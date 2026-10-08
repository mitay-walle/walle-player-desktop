import os
import struct
import wave
from pathlib import Path

import pytest
from mutagen.id3 import APIC, ID3
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3

# jobs normally run in a child process; tests run them in a thread so monkeypatches still apply
# (test_jobs_in_a_process covers the real process path)
os.environ.setdefault("MUSICPLAYER_JOBS_INPROC", "1")

from musicplayer.core.db import Database


def make_mp3(path: Path, **tags) -> Path:
    """Write a minimal valid MPEG audio file and tag it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = bytes([0xFF, 0xFB, 0x90, 0x00]) + b"\x00" * 413  # 128 kbps / 44.1 kHz layer III frame
    path.write_bytes(frame * 40)
    audio = MP3(path)
    audio.add_tags() if audio.tags is None else None
    audio.save(v2_version=3)
    easy = EasyID3(path)
    for k, v in tags.items():
        easy[k] = v
    easy.save(v2_version=3)
    return path


def add_cover(path: Path, data: bytes) -> None:
    t = ID3(path)
    t.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=data))
    t.save(v2_version=3)


def tiny_jpeg() -> bytes:
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def db():
    d = Database()
    yield d
    d.close()


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "музыка"
    make_mp3(root / "Кино" / "Группа крови (1988)" / "01 - Группа крови.mp3", artist="Кино",
             albumartist="Кино", album="Группа крови", title="Группа крови", tracknumber="1/9",
             date="1988", genre=["Rock", "Post-Punk"])
    make_mp3(root / "Кино" / "Группа крови (1988)" / "02 - Закрой за мной дверь.mp3", artist="Кино",
             albumartist="Кино", album="Группа крови", title="Закрой за мной дверь", tracknumber="2/9",
             date="1988", genre=["Rock"])
    make_mp3(root / "Beatles" / "Yesterday.mp3", artist="Beatles", title="Yesterday", date="1965",
             genre=["Pop"])
    add_cover(root / "Кино" / "Группа крови (1988)" / "01 - Группа крови.mp3", tiny_jpeg())
    return root
