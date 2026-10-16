"""The Windows version resource (Properties → Details) for Walle Player's executables.

Code signing services check it: product name, version and description must be filled in and the same on every signed
file of a release.  Used by musicplayer.spec (WallePlayer.exe) and scripts/build_installer.py (the installer).
"""
from __future__ import annotations

from pathlib import Path

PRODUCT = "Walle Player"
COMPANY = "Walle Player"
COPYRIGHT = "GPL-3.0-or-later, github.com/mitay-walle/walle-player-desktop"


def write(path: Path, version: str, description: str, filename: str) -> Path:
    """Writes a PyInstaller version file (``EXE(version=...)`` / ``--version-file``) and returns its path."""
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                     VarFileInfo, VarStruct, VSVersionInfo)
    nums = tuple((list(map(int, version.split(".")[:4])) + [0, 0, 0, 0])[:4])
    info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums, mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
        kids=[StringFileInfo([StringTable("040904B0", [
                  StringStruct("CompanyName", COMPANY),
                  StringStruct("FileDescription", description),
                  StringStruct("FileVersion", version),
                  StringStruct("InternalName", Path(filename).stem),
                  StringStruct("LegalCopyright", COPYRIGHT),
                  StringStruct("OriginalFilename", filename),
                  StringStruct("ProductName", PRODUCT),
                  StringStruct("ProductVersion", version)])]),
              VarFileInfo([VarStruct("Translation", [0x0409, 1200])])])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(info), encoding="utf-8")
    return path
