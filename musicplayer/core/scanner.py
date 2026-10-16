"""Library scanner: reads tags and covers with mutagen, incremental by mtime/size."""

from __future__ import annotations

import hashlib
import io
import os
import time
import re
from pathlib import Path
from typing import Callable

import mutagen
from mutagen.id3 import ID3
from PIL import Image

from .db import Database, split_genres

AUDIO_EXT = {".mp3", ".flac", ".ogg", ".m4a", ".wav", ".opus"}
COVER_NAMES = ("cover.jpg", "cover.png", "folder.jpg", "folder.png", "front.jpg")
THUMB = 300


def _first(tags, key: str) -> str:
    v = tags.get(key) if tags else None
    return str(v[0]).strip() if v else ""


def _num(s: str) -> int | None:
    m = re.match(r"\s*(\d+)", s or "")
    return int(m.group(1)) if m else None


def _year(s: str) -> int | None:
    m = re.search(r"(\d{4})", s or "")
    return int(m.group(1)) if m else None


def _folder_artist(path: Path, root: Path | None) -> str:
    """Artist from the layout <root>/<Artist>/[<Album>/]<file> when the tag is missing."""
    try:
        parts = path.relative_to(root).parts if root else ()
    except ValueError:
        parts = ()
    return parts[0] if len(parts) >= 2 else path.parent.name


def read_tags(path: Path, root: Path | None = None) -> dict:
    st = path.stat()
    d: dict = {"path": str(path), "mtime": st.st_mtime, "size": st.st_size}
    audio = mutagen.File(path, easy=True)
    tags = audio.tags if audio is not None else None
    d["duration"] = float(audio.info.length) if audio is not None and audio.info else None
    d["title"] = _first(tags, "title") or path.stem
    d["artist"] = _first(tags, "artist") or _folder_artist(path, root)
    d["albumartist"] = _first(tags, "albumartist")
    d["album"] = _first(tags, "album")
    d["year"] = _year(_first(tags, "date"))
    d["track_no"] = _num(_first(tags, "tracknumber"))
    d["disc_no"] = _num(_first(tags, "discnumber"))
    d["_genres"] = split_genres(list(tags.get("genre", [])) if tags else [])
    return d


def extract_cover(path: Path) -> bytes | None:
    """Embedded picture first, then cover.jpg/folder.jpg in the track's folder."""
    try:
        if path.suffix.lower() == ".mp3":
            for k, fr in ID3(path).items():
                if k.startswith("APIC"):
                    return fr.data
        else:
            f = mutagen.File(path)
            pics = getattr(f, "pictures", None)
            if pics:
                return pics[0].data
            if f is not None and f.tags and "covr" in f.tags:
                return bytes(f.tags["covr"][0])
    except Exception:
        pass
    for n in COVER_NAMES:
        p = path.parent / n
        if p.is_file():
            return p.read_bytes()
    return None


def store_cover(data: bytes, cover_dir: Path) -> str | None:
    key = hashlib.sha1(data).hexdigest()[:20]
    out = cover_dir / f"{key}.jpg"
    if out.exists():
        return key
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
        im.thumbnail((THUMB, THUMB))
        cover_dir.mkdir(parents=True, exist_ok=True)
        im.save(out, "JPEG", quality=85)
        return key
    except Exception:
        return None


def iter_audio(root: Path):
    for dp, _dn, files in os.walk(root):
        for f in files:
            if Path(f).suffix.lower() in AUDIO_EXT:
                yield Path(dp) / f


def _eta(seconds: float) -> str:
    s = int(seconds)
    return f"~{s // 60} мин {s % 60:02d} с" if s >= 60 else f"~{max(s, 1)} с"


def scan(db: Database, root: str | Path, cover_dir: str | Path,
         progress: Callable[[int, int, str], None] | None = None, full: bool = False) -> dict:
    """Scan ``root``: new/changed files are (re)read, vanished ones marked missing.

    ``progress(done, total, message)``: first the file list is collected (total 0, «найдено N файлов»), then every
    file is checked against the catalogue (done / total, what is new or changed, the folder, time left)."""
    root, cover_dir = Path(root), Path(cover_dir)
    if not root.is_dir():  # an unplugged drive must never mark the whole library as missing
        raise FileNotFoundError(f"Папка не найдена: {root}")
    known = db.known_files()
    seen: set[str] = set()
    added = updated = 0
    errors: list[tuple[str, str]] = []
    files: list[Path] = []
    t_list = 0.0
    for p in iter_audio(root):                           # listing is fast (seconds), and gives the real total
        files.append(p)
        if progress and time.monotonic() - t_list > 0.25:
            t_list = time.monotonic()
            progress(0, 0, f"Ищу файлы: найдено {len(files)}")
    total = len(files)
    db.conn.execute("BEGIN")
    t_commit = t0 = t_prog = time.monotonic()
    try:
        for i, p in enumerate(files, 1):
            if progress and (time.monotonic() - t_prog > 0.2 or i == total):
                t_prog = time.monotonic()
                el = t_prog - t0
                left = f" · осталось {_eta(el / i * (total - i))}" if i > 20 and el > 2 else ""
                news = ", ".join(x for x in (f"новых {added}" if added else "", f"изменено {updated}" if updated
                                             else "") if x) or "без изменений"
                progress(i, total, f"{news}{left} · {p.parent.parent.name}/{p.parent.name}")
            if time.monotonic() - t_commit > 0.3:       # short write transactions: the player and the library
                db.conn.execute("COMMIT")                # server must be able to write (plays, labels) meanwhile
                db.conn.execute("BEGIN")
                t_commit = time.monotonic()
            sp = str(p)
            seen.add(sp)
            st = p.stat()
            old = known.get(sp)
            if not full and old and old[0] == st.st_mtime and old[1] == st.st_size:
                continue
            try:
                d = read_tags(p, root)
                data = extract_cover(p)
                d["cover_key"] = store_cover(data, cover_dir) if data else None
                db.upsert_track(d, d.pop("_genres"))
                if old is None:
                    added += 1
                else:
                    updated += 1
            except Exception as e:  # an unreadable file must not abort the scan
                errors.append((sp, str(e)))
        if progress:
            progress(total, total, f"новых {added}, изменено {updated} · сверяю пропавшие")
        prefix = str(root)
        gone = [p for p in known if p not in seen and p.startswith(prefix)]
        if not seen and gone:
            raise RuntimeError("В папке не найдено ни одного аудиофайла, а в каталоге они есть: "
                               "диск недоступен? Каталог не изменён.")
        db.mark_missing(gone)
        db.conn.execute("COMMIT")
    except BaseException:
        db.conn.execute("ROLLBACK")
        raise
    return {"added": added, "updated": updated, "missing": len(gone), "errors": errors, "total": len(seen)}
