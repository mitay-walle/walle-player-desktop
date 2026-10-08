"""Locations of bundled resources and per-user data."""

from __future__ import annotations

import os
from pathlib import Path


def resource_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "resources"


def data_dir() -> Path:
    """Database, covers, models.  Override with MUSICPLAYER_HOME."""
    if os.environ.get("MUSICPLAYER_HOME"):
        p = Path(os.environ["MUSICPLAYER_HOME"])
    else:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        p, old = base / "WallePlayer", base / "MusicPlayer"
        if old.exists() and not p.exists():          # the app used to be called MusicPlayer: keep its data
            try:
                old.rename(p)
            except OSError:
                p = old
    p.mkdir(parents=True, exist_ok=True)
    return p
