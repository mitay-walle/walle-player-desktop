"""Audio decoding helpers built on ``soundfile`` (libsndfile) and ``scipy``.

Everything here reads only what it needs: ``load_mono`` reads a single frame
window, ``waveform_peaks`` streams block by block. Nothing imports torch.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
from pathlib import Path

import numpy as np
import soundfile as sf

from musicplayer.core.jobs import JobContext

log = logging.getLogger(__name__)

PEAKS_BLOCK = 65536


def _window(total_frames: int, sr: int, max_seconds: float | None, offset_mode: str) -> tuple[int, int]:
    """Return ``(start, frames)``; ``frames == -1`` means "read everything"."""
    if max_seconds is None or max_seconds <= 0 or total_frames <= 0 or sr <= 0:
        return 0, -1
    want = int(round(max_seconds * sr))
    if want <= 0:
        return 0, -1
    if want >= total_frames:
        return 0, -1
    if offset_mode == "center":
        start = (total_frames - want) // 2
    elif offset_mode == "start":
        start = 0
    else:
        raise ValueError(f"unknown offset_mode {offset_mode!r}")
    return start, want


def resample(samples: np.ndarray, sr: int, target_sr: int) -> np.ndarray:
    """Polyphase resampling (``scipy.signal.resample_poly``) with gcd-reduced factors."""
    if sr == target_sr or samples.size == 0:
        return samples.astype(np.float32, copy=False)
    from scipy.signal import resample_poly  # scipy is fine here (§2), lazy anyway

    g = math.gcd(int(target_sr), int(sr))
    up, down = int(target_sr) // g, int(sr) // g
    out = resample_poly(samples.astype(np.float64), up, down, axis=0)
    return np.asarray(out, dtype=np.float32)


def load_mono(
    path: str | Path,
    target_sr: int | None = None,
    max_seconds: float | None = None,
    offset_mode: str = "start",
) -> tuple[np.ndarray, int]:
    """Load a float32 mono window of ``path`` in ``[-1, 1]``.

    Only the requested window (``max_seconds`` from the start or the middle) is
    read from disk; mix-down and resampling happen on that window only.
    Decoding errors propagate (callers treat them as failed predictions).
    """
    with sf.SoundFile(str(path)) as f:
        sr = int(f.samplerate)
        start, frames = _window(int(f.frames), sr, max_seconds, offset_mode)
        if start > 0:
            f.seek(start)
        data = f.read(frames=frames, dtype="float32", always_2d=True)
    if data.ndim == 2:
        mono = data[:, 0] if data.shape[1] == 1 else data.mean(axis=1)
    else:
        mono = data
    mono = np.ascontiguousarray(mono, dtype=np.float32)
    out_sr = sr
    if target_sr is not None and int(target_sr) != sr:
        mono = resample(mono, sr, int(target_sr))
        out_sr = int(target_sr)
    np.clip(mono, -1.0, 1.0, out=mono)
    return mono, out_sr


def waveform_peaks(path: str | Path, columns: int = 600, ctx: JobContext | None = None) -> np.ndarray:
    """Min/max envelope of ``path`` with shape ``(columns, 2)`` (streamed, one block in memory)."""
    columns = max(1, int(columns))
    mins = np.full(columns, np.inf, dtype=np.float32)
    maxs = np.full(columns, -np.inf, dtype=np.float32)
    with sf.SoundFile(str(path)) as f:
        total = int(f.frames)
        if total <= 0:
            return np.zeros((columns, 2), dtype=np.float32)
        pos = 0
        for block in f.blocks(blocksize=PEAKS_BLOCK, dtype="float32", always_2d=True):
            if ctx is not None:
                ctx.check()
            n = block.shape[0]
            if n == 0:
                continue
            fmin = block.min(axis=1)
            fmax = block.max(axis=1)
            cols = (np.arange(pos, pos + n, dtype=np.int64) * columns) // total
            np.clip(cols, 0, columns - 1, out=cols)
            uniq, idx = np.unique(cols, return_index=True)
            mins[uniq] = np.minimum(mins[uniq], np.minimum.reduceat(fmin, idx))
            maxs[uniq] = np.maximum(maxs[uniq], np.maximum.reduceat(fmax, idx))
            pos += n
    mins[~np.isfinite(mins)] = 0.0
    maxs[~np.isfinite(maxs)] = 0.0
    return np.stack([mins, maxs], axis=1).astype(np.float32)


def peaks_cache_path(cache_dir: Path, path: str | Path) -> Path:
    """``cache_dir/peaks/<sha1(resolved|mtime)>.npy`` (the directory is created)."""
    resolved = str(Path(path).resolve())
    try:
        mtime = os.stat(resolved).st_mtime
    except OSError:
        mtime = 0.0
    digest = hashlib.sha1(f"{resolved}|{mtime}".encode("utf-8")).hexdigest()
    folder = Path(cache_dir) / "peaks"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.warning("cannot create peaks cache dir %s", folder)
    return folder / f"{digest}.npy"


def rms_dbfs(samples: np.ndarray) -> float:
    """RMS level in dBFS; ``-inf`` for empty or all-zero input. Never raises."""
    try:
        arr = np.asarray(samples, dtype=np.float64)
        if arr.size == 0:
            return float("-inf")
        rms = math.sqrt(float(np.mean(np.square(arr))))
        if not math.isfinite(rms) or rms <= 0.0:
            return float("-inf")
        return 20.0 * math.log10(rms)
    except Exception:  # noqa: BLE001
        return float("-inf")


def format_duration(seconds: float | None) -> str:
    """``"1:05.3"`` style; ``"—"`` for ``None`` / invalid values."""
    if seconds is None:
        return "—"
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(value) or value < 0:
        return "—"
    total = round(value, 1)
    minutes = int(total // 60)
    rest = total - minutes * 60
    if rest >= 59.95:  # rounding guard: 59.97 -> "1:00.0"
        minutes += 1
        rest = 0.0
    return f"{minutes}:{rest:04.1f}"
