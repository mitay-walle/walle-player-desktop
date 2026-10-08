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


def trash_tracks(db, tracks: list[dict], remover=None) -> dict:
    """Recycle the files of ``tracks`` and mark them missing in the catalogue.  Returns {'deleted': n, 'failed': [paths]}."""
    remover = remover or to_recycle_bin
    done, failed = [], []
    for t in tracks:
        (done if remover(t["path"]) else failed).append(t["path"])
    db.mark_missing(done)
    db.conn.commit()
    return {"deleted": len(done), "failed": failed}
