"""Move files to the Windows Recycle Bin (never a hard delete: the user can restore them from the bin)."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

FO_DELETE = 3
FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 0x4, 0x10, 0x40, 0x400


class _FileOp(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR)]


def to_recycle_bin(path: str) -> bool:
    """True when the file is gone from its folder (in the Recycle Bin).  A missing file counts as done."""
    if not os.path.exists(path):
        return True
    op = _FileOp(None, FO_DELETE, os.path.abspath(path) + "\0", None,
                 FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI, False, None, None)
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted


def to_trash_folder(path: str, root: str | None) -> bool:
    """Linux (a home server): move the file into ``.walle-trash`` next to the music folder ``root``, keeping its
    folders, so it can be put back; outside ``root`` (or without it) into ``.walle-trash`` beside the file."""
    import shutil
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return True
    try:
        if root and p.is_relative_to(root):
            dest = Path(root).parent / ".walle-trash" / p.relative_to(root)
        else:
            dest = p.parent / ".walle-trash" / p.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest = dest.with_name(f"{dest.stem}.{int(__import__('time').time())}{dest.suffix}")
        shutil.move(str(p), str(dest))
        return True
    except OSError:
        return False


def remove_file(path: str, root: str | None = None) -> bool:
    """The Recycle Bin on Windows, the trash folder elsewhere."""
    return to_recycle_bin(path) if os.name == "nt" else to_trash_folder(path, root)


def trash_tracks(db, tracks: list[dict], remover=None) -> dict:
    """Recycle the files of ``tracks`` and mark them missing in the catalogue.  Returns {'deleted': n, 'failed': [paths]}."""
    remover = remover or to_recycle_bin
    done, failed = [], []
    for t in tracks:
        (done if remover(t["path"]) else failed).append(t["path"])
    db.mark_missing(done)
    db.conn.commit()
    return {"deleted": len(done), "failed": failed}
