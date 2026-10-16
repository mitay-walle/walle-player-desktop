"""Freesound-compatible audio descriptors computed locally (numpy / scipy / librosa).

The goal is to describe every file of the library with the *same descriptor
names and (as far as practical) the same scales* that the Freesound API returns
(https://freesound.org/docs/api/analysis_docs.html), so local sounds can be
filtered, compared and later matched against Freesound results.  Definitions
follow Essentia's ``FreesoundExtractor`` (the analyzer Freesound runs) and the
AudioCommons extractor; differences are listed below.

Public API (Qt-free, cheap to import — numpy only at module level)
------------------------------------------------------------------
``DESCRIPTOR_NAMES``
    Ordered tuple of every key :func:`extract_descriptors` returns.
``NUMERIC_DESCRIPTORS`` / ``TIMBRAL_DESCRIPTORS`` / ``STRING_DESCRIPTORS`` / ``BOOL_DESCRIPTORS``
    Subsets of the above.
``extract_descriptors(path, *, max_seconds=30.0, sr=44100, timbral=True, ctx=None, timings=None)``
    Decode ``path`` (centre window of at most ``max_seconds``, mono, resampled to
    ``sr``) and return ``{name: float | int | str | bool | None}`` with exactly
    the keys of ``DESCRIPTOR_NAMES``.  Values are JSON-serialisable (no NaN/inf:
    unknown -> ``None``).  Decoding errors propagate; every descriptor group is
    isolated, so one failing computation only yields ``None`` for its keys.
    ``ctx`` (:class:`~musicplayer.core.jobs.JobContext`) receives per-stage
    progress and is checked between stages (cancellation raises
    :class:`~musicplayer.core.jobs.CancelledError`).  If ``timings`` is a dict it
    receives the wall time (s) of every stage.
``extract_from_signal(samples, sr, *, timbral=True, ctx=None, timings=None, truncated=False, start_window=None)``
    Same on an in-memory mono float signal (used by tests and by callers that
    already decoded the file).
``descriptor_vector(desc, names, *, log_scale=True) -> np.ndarray``
    Numeric float64 vector for ``names`` (bool -> 0/1, str / missing -> NaN;
    descriptors flagged ``"log": true`` in the catalog are log10-compressed so
    z-scores are meaningful).
``standardize(matrix) -> (z, mean, std)``
    NaN-aware column z-scores (constant columns get std 1).
``nearest(query_vec, matrix, k=10, *, exclude=None) -> list[(row, similarity)]``
    Cosine similarity on standardized vectors, NaN-safe (NaN -> column mean ->
    0 after standardization); best first.  "Find similar sounds".
``load_catalog()`` / ``descriptor_label(name, lang)`` / ``descriptor_unit(name)``
    Metadata from ``resources/descriptors.json`` (group, type, unit, range hint,
    ru/en display names and descriptions).

Definitions and approximations
------------------------------
* Analysis signal: mono, ``sr`` = 44.1 kHz (Freesound), at most ``max_seconds``
  (centre window).  Frame-based descriptors use frame 2048 / hop 1024, Hann
  window normalised like Essentia's ``Windowing(normalized=True)`` (a full-scale
  sine gives a peak magnitude ~1); tonal ones (HPCP, key) use 4096 / 2048.
  Frame statistics are means over *non-silent* frames (power >= -70 dB); Essentia
  averages all frames, which makes values of sounds with long silent tails drift
  towards zero — this is a deliberate deviation.
* Envelope: Essentia ``Envelope`` (attack 10 ms, release 1500 ms, rectified,
  one-pole follower), evaluated on 8-sample peak blocks for speed.
  ``log_attack_time`` (thresholds 20 % / 90 % of max), ``amplitude_peak_ratio``
  (MaxToTotal), ``temporal_centroid_ratio`` (TCToTotal), ``duration_effective``
  (EffectiveDuration, thresholdRatio 0.1, -90 dB floor), ``decay_strength``
  (StrongDecay = sqrt(energy / centroid[s])) and ``temporal_centroid`` /
  ``temporal_spread`` / ``temporal_skewness`` / ``temporal_decrease``
  (Centroid / CentralMoments+DistributionShape / Decrease with Essentia's
  default ``range=1``, i.e. on a normalised 0..1 time axis — that is why
  Freesound documents ``temporal_centroid`` as 0..1) are computed on it.
* Spectral: centroid (Hz), spread (variance, Hz^2), skewness, roll-off (85 % of
  energy), flatness (geometric / arithmetic mean of the magnitude spectrum, 0..1),
  crest (max / mean), energy (sum of squared magnitudes), entropy (Shannon,
  log2, on the normalised magnitude spectrum), complexity (number of spectral
  peaks 100-5000 Hz above magnitude 0.005, max 100), zero-crossing rate.
* Pitch: YIN (cumulative-mean-normalised difference, FFT based — close to
  Essentia's PitchYinFFT) on 2048-sample frames, 30-5000 Hz;
  ``pitch_confidence`` = mean (1 - dip depth) over non-silent frames;
  ``pitch``/``pitch_min``/``pitch_max``/``pitch_var`` over frames with
  confidence >= 0.5 (all non-silent frames if none);  ``note_*`` from the
  median pitch / median confidence (AudioCommons); ``pitch_salience``
  = Essentia PitchSalience (max autocorrelation of the magnitude spectrum for
  lags 100-5000 Hz / autocorrelation at lag 0) — note a pure sine has *low*
  salience by that definition.
* Tonal: ``dissonance`` = Essentia Dissonance (Plomp-Levelt, A-weighted peak
  loudness) and ``inharmonicity`` = Essentia Inharmonicity on HarmonicPeaks
  (20 harmonics, tolerance 0.2) of up to 300 evenly spaced frames;
  ``hpcp_crest`` / ``hpcp_entropy`` = means of the per-frame Crest / Entropy of a
  36-bin HPCP (peaks 40-5000 Hz, squared-cosine 1-semitone weighting,
  unit-max); ``tonality`` / ``tonality_confidence`` = Krumhansl-Kessler
  major/minor profile correlation with the mean HPCP folded to 12 classes
  (Essentia's KeyExtractor uses other profiles; values differ slightly).
* Level: ``loudness`` = ITU-R BS.1770 / EBU R128 integrated loudness (K-weighting,
  400 ms blocks, 75 % overlap, -70 LUFS absolute and -10 LU relative gates;
  clips shorter than 400 ms use one block); ``dynamic_range`` = EBU loudness
  range (3 s short-term windows every 100 ms, -70 / -20 gates, P95 - P10; 0 for
  clips shorter than 3 s); ``silence_rate`` = fraction of frames with power
  below -30 dB (Essentia SilenceRate 30 dB); ``start_time`` = centre of the first
  frame with power above -60 dB (StartStopSilence), computed on the first
  ``max_seconds`` of the file when the analysis window was truncated.
* Rhythm (librosa): ``onset_count`` (spectral-flux onsets; peak threshold
  max(10 % of the strongest flux, 1 dB) so steady tones report 0), ``bpm`` / ``beat_count``
  (``librosa.beat.beat_track``; ``bpm`` is ``None`` with fewer than 2 beats),
  ``bpm_confidence`` = normalised onset-envelope autocorrelation at the beat
  period (Essentia's RhythmExtractor2013 confidence is a different 0..5.32
  scale), ``beat_loudness`` = mean signal power in a 100 ms window around each
  beat (approximation of BeatsLoudness).
* ``single_event``: AudioCommons ``estimate_number_of_events`` (Envelope 15/50 ms,
  activity regions above 4.5 x mean envelope energy, region energy > 0.5,
  regions closer than 50 ms merged; False for clips > 7 s).
* ``loopable``: AudioCommons rule ``LoopBpmConfidence(bpm) > 0.95`` (envelope
  10 ms, 5 % threshold, four duration variants vs multiples of the beat
  duration), additionally requiring >= 2 onsets and not ``single_event``;
  ``None`` when the analysis window was truncated (the whole file is unknown).
* Timbral (AudioCommons ``timbral_models`` 0.4 through
  :mod:`musicplayer.core._timbral_compat`): boominess, brightness, depth,
  hardness, roughness, sharpness, warmth (0..100, unclipped) and reverbness
  (bool), on the centre 10 s of the analysis window.  ``None`` if the package
  is missing or ``timbral=False``.  Slow (~5-10 s per 10 s clip).

Performance (RTX-3050 box, CPU only): ~0.6 s per 10 s clip without timbral
(harmonic + tonal stages dominate); the first call in a process additionally
pays a one-time ~5-9 s for importing librosa/scipy/numba and JIT-compiling the
beat tracker, so run extraction in one long-lived worker.

Silent or empty input never raises: everything is ``None`` except
``silence_rate`` (1.0), ``onset_count`` / ``beat_count`` (0) and
``single_event`` / ``loopable`` (False).
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Callable, Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from musicplayer.core.jobs import CancelledError, JobContext

log = logging.getLogger(__name__)

DescriptorValue = float | int | str | bool | None

TEMPORAL_DESCRIPTORS: tuple[str, ...] = (
    "amplitude_peak_ratio", "decay_strength", "duration_effective", "log_attack_time",
    "temporal_centroid", "temporal_centroid_ratio", "temporal_decrease", "temporal_skewness",
    "temporal_spread", "start_time",
)
SPECTRAL_DESCRIPTORS: tuple[str, ...] = (
    "spectral_centroid", "spectral_complexity", "spectral_crest", "spectral_energy",
    "spectral_entropy", "spectral_flatness", "spectral_rolloff", "spectral_skewness",
    "spectral_spread", "zero_crossing_rate",
)
TONAL_DESCRIPTORS: tuple[str, ...] = (
    "dissonance", "inharmonicity", "hpcp_crest", "hpcp_entropy",
    "pitch", "pitch_min", "pitch_max", "pitch_var", "pitch_confidence", "pitch_salience",
    "note_name", "note_midi", "note_confidence", "tonality", "tonality_confidence",
)
LEVEL_DESCRIPTORS: tuple[str, ...] = ("loudness", "dynamic_range", "silence_rate")
RHYTHM_DESCRIPTORS: tuple[str, ...] = (
    "onset_count", "single_event", "loopable", "bpm", "bpm_confidence", "beat_count", "beat_loudness",
)
TIMBRAL_DESCRIPTORS: tuple[str, ...] = (
    "boominess", "brightness", "depth", "hardness", "roughness", "sharpness", "warmth", "reverbness",
)
DESCRIPTOR_NAMES: tuple[str, ...] = (
    TEMPORAL_DESCRIPTORS + SPECTRAL_DESCRIPTORS + TONAL_DESCRIPTORS + LEVEL_DESCRIPTORS
    + RHYTHM_DESCRIPTORS + TIMBRAL_DESCRIPTORS
)
STRING_DESCRIPTORS: frozenset[str] = frozenset({"note_name", "tonality"})
BOOL_DESCRIPTORS: frozenset[str] = frozenset({"single_event", "loopable", "reverbness"})
INT_DESCRIPTORS: frozenset[str] = frozenset({"note_midi", "onset_count", "bpm", "beat_count"})
NUMERIC_DESCRIPTORS: tuple[str, ...] = tuple(n for n in DESCRIPTOR_NAMES if n not in STRING_DESCRIPTORS)

NOTE_NAMES: tuple[str, ...] = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
KEY_NAMES: tuple[str, ...] = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")  # Essentia spelling

# analysis constants
FRAME = 2048
HOP = 1024
TONAL_FRAME = 4096
TONAL_HOP = 2048
ENV_BLOCK = 8
ACTIVE_POWER = 1e-7          # -70 dB: frames below are ignored for frame statistics
SILENCE_POWER = 1e-3         # -30 dB: Essentia SilenceRate 30 dB
START_POWER = 1e-6           # -60 dB: StartStopSilence threshold
NOISE_FLOOR = 10 ** (-90 / 20)
MAX_TONAL_FRAMES = 300
PITCH_FMIN = 30.0
PITCH_FMAX = 5000.0
TIMBRAL_MAX_SECONDS = 10.0   # timbral_models is slow: analyse the centre 10 s only
VOICED_CONFIDENCE = 0.5
_EPS = 1e-20


# =========================================================================== catalog
@lru_cache(maxsize=1)
def load_catalog() -> dict[str, dict[str, Any]]:
    """``resources/descriptors.json`` as ``{name: meta}`` (empty dict if unreadable)."""
    from musicplayer.core.paths import resource_dir

    path = resource_dir() / "descriptors.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("cannot read descriptor catalog %s: %s", path, exc)
        return {}
    return dict(data.get("descriptors", {}))


def descriptor_label(name: str, lang: str = "en") -> str:
    """Display name of ``name`` in ``lang`` (falls back to English, then to the id)."""
    meta = load_catalog().get(name) or {}
    names = meta.get("name") or {}
    return str(names.get(lang) or names.get("en") or name)


def descriptor_unit(name: str) -> str:
    return str((load_catalog().get(name) or {}).get("unit") or "")


# =========================================================================== helpers
def _clean(value: Any) -> DescriptorValue:
    """Convert numpy scalars to JSON-safe Python values (non-finite -> None)."""
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (int, np.integer)):
        return int(value)
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _empty_result() -> dict[str, DescriptorValue]:
    return {name: None for name in DESCRIPTOR_NAMES}


def _frames(x: np.ndarray, size: int, hop: int) -> np.ndarray:
    """Centred frames (Essentia FrameCutter ``startFromZero=False``), shape ``(n, size)``."""
    half = size // 2
    xp = np.pad(x, (half, half))
    if xp.size < size:
        xp = np.pad(xp, (0, size - xp.size))
    view = np.lib.stride_tricks.sliding_window_view(xp, size)[::hop]
    # keep frames whose centre lies inside the signal
    n_keep = max(1, min(view.shape[0], (x.size - 1) // hop + 1 if x.size else 1))
    return view[:n_keep]


def _window(size: int) -> np.ndarray:
    w = 0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(size) / size)  # periodic Hann
    return w * (2.0 / w.sum())


def _magnitudes(frames: np.ndarray) -> np.ndarray:
    return np.abs(np.fft.rfft(frames * _window(frames.shape[1]), axis=1))


def _envelope(x: np.ndarray, sr: int, attack_ms: float = 10.0, release_ms: float = 1500.0,
              block: int = ENV_BLOCK) -> tuple[np.ndarray, float]:
    """Essentia ``Envelope`` on ``block``-sample peak blocks -> ``(envelope, envelope_rate)``."""
    n = x.size
    if n == 0:
        return np.zeros(0), sr / block
    pad = (-n) % block
    peaks = np.abs(np.pad(x, (0, pad))).reshape(-1, block).max(axis=1).astype(np.float64)
    a = math.exp(-block / (attack_ms * 1e-3 * sr)) if attack_ms > 0 else 0.0
    r = math.exp(-block / (release_ms * 1e-3 * sr)) if release_ms > 0 else 0.0
    out = [0.0] * peaks.size
    env = 0.0
    a1, r1 = 1.0 - a, 1.0 - r
    for i, p in enumerate(peaks.tolist()):
        env = (a * env + a1 * p) if p > env else (r * env + r1 * p)
        out[i] = env
    return np.asarray(out), sr / block


def _central_shape(values: np.ndarray, axis_values: np.ndarray) -> tuple[float, float, float]:
    """(centroid, spread = 2nd central moment, skewness) of a non-negative distribution."""
    total = float(values.sum())
    if total <= 0:
        return float("nan"), float("nan"), float("nan")
    c = float((values * axis_values).sum() / total)
    d = axis_values - c
    m2 = float((values * d * d).sum() / total)
    m3 = float((values * d ** 3).sum() / total)
    skew = m3 / m2 ** 1.5 if m2 > 0 else 0.0
    return c, m2, skew


def _hz2bark(f: np.ndarray) -> np.ndarray:
    b = 26.81 * f / (1960.0 + f) - 0.53
    b = np.where(b < 2, b + 0.15 * (2 - b), b)
    return np.where(b > 20.1, b + 0.22 * (b - 20.1), b)


def _bark2hz(z: np.ndarray) -> np.ndarray:
    z = np.where(z < 2, (z - 0.3) / 0.85, z)
    z = np.where(z > 20.1, (z + 4.422) / 1.22, z)
    return 1960.0 * (z + 0.53) / (26.28 - z)


def _bark_cbw(z: np.ndarray) -> np.ndarray:
    return 52548.0 / (z * z - 52.56 * z + 690.39)


def _a_weighting(f: np.ndarray) -> np.ndarray:
    f2 = f * f
    return (1.25893 * 12200.0 ** 2 * f2 * f2) / (
        (f2 + 20.6 ** 2) * (f2 + 12200.0 ** 2) * np.sqrt(f2 + 107.7 ** 2) * np.sqrt(f2 + 737.9 ** 2))


def _plomp_levelt(df: np.ndarray) -> np.ndarray:
    res = (-6.58977878 * df ** 5 + 28.58224226 * df ** 4 - 47.36739986 * df ** 3
           + 35.70679761 * df ** 2 - 10.36526344 * df + 1.00026609)
    res = np.clip(res, 0.0, 1.0)
    return np.where((df < 0) | (df > 1.18), 1.0, res)


def _spectral_peaks(mag: np.ndarray, freqs: np.ndarray, fmin: float, fmax: float,
                    threshold: float, max_peaks: int = 100) -> tuple[np.ndarray, np.ndarray]:
    """Local maxima with parabolic interpolation, strongest ``max_peaks``, sorted by frequency."""
    if mag.size < 3:
        return np.zeros(0), np.zeros(0)
    m = mag
    k = np.flatnonzero((m[1:-1] > m[:-2]) & (m[1:-1] >= m[2:]) & (m[1:-1] > threshold)) + 1
    if k.size == 0:
        return np.zeros(0), np.zeros(0)
    a, b, c = m[k - 1], m[k], m[k + 1]
    den = a - 2 * b + c
    with np.errstate(divide="ignore", invalid="ignore"):
        delta = np.where(np.abs(den) > _EPS, 0.5 * (a - c) / den, 0.0)
    delta = np.clip(delta, -0.5, 0.5)
    df = freqs[1] - freqs[0]
    pf = (k + delta) * df
    pm = b - 0.25 * (a - c) * delta
    sel = (pf >= fmin) & (pf <= fmax)
    pf, pm = pf[sel], pm[sel]
    if pf.size > max_peaks:
        top = np.argsort(pm)[::-1][:max_peaks]
        pf, pm = pf[top], pm[top]
    order = np.argsort(pf)
    return pf[order], pm[order]


def _dissonance(f: np.ndarray, m: np.ndarray) -> float:
    """Essentia Dissonance of one frame's spectral peaks (sorted by frequency)."""
    if f.size < 2:
        return 0.0
    loud = m * _a_weighting(f) ** 2
    total = float(loud.sum())
    if total <= 0:
        return 0.0
    bark = _hz2bark(f)
    end_f = _bark2hz(bark + 1.18)
    cbw = _bark_cbw(bark)
    f1, f2 = f[:, None], f[None, :]
    mask = (f1 > 50) & (f2 >= 50) & (f2 < end_f[:, None]) & (f2 < 10000)
    df = np.abs(f2 - f1) / np.minimum(cbw[:, None], cbw[None, :])
    d = 1.0 - _plomp_levelt(df)
    contrib = np.where(mask & (d > 0), d * (loud[:, None] + loud[None, :]) / total, 0.0)
    per_peak = np.minimum(contrib.sum(axis=1), loud / total)
    per_peak = np.where(f > 50, per_peak, 0.0)
    return float(per_peak.sum() / 2.0)


def _harmonic_peaks(f: np.ndarray, m: np.ndarray, f0: float, max_harmonics: int = 20,
                    tolerance: float = 0.2) -> tuple[np.ndarray, np.ndarray]:
    """Essentia HarmonicPeaks: the peak closest to each harmonic ``h * f0`` within ``tolerance``."""
    out_f: list[float] = [f0]
    out_m: list[float] = [0.0]
    if f.size == 0 or f0 <= 0:
        return np.asarray(out_f), np.asarray(out_m)
    ratio = f / f0
    h = np.round(ratio)
    err = np.abs(ratio - h)
    ok = (h >= 1) & (h <= max_harmonics) & (err <= tolerance)
    best: dict[int, tuple[float, float, float]] = {}
    for fi, mi, hi, ei in zip(f[ok], m[ok], h[ok].astype(int), err[ok]):
        if hi not in best or ei < best[hi][2]:
            best[hi] = (float(fi), float(mi), float(ei))
    if 1 in best:
        out_f[0], out_m[0] = best[1][0], best[1][1]
    for hi in sorted(k for k in best if k > 1):
        fi, mi, _ = best[hi]
        if fi > out_f[-1]:
            out_f.append(fi)
            out_m.append(mi)
    return np.asarray(out_f), np.asarray(out_m)


def _inharmonicity(f: np.ndarray, m: np.ndarray) -> float:
    if f.size == 0 or f[0] <= 0:
        return float("nan")
    f0 = f[0]
    m2 = m * m
    den = float(m2.sum())
    if den <= 0:
        return 1.0
    num = float((np.abs(f[1:] - np.round(f[1:] / f0) * f0) * m2[1:]).sum())
    return num / (den * f0)


# =========================================================================== stages
def _stage_temporal(x: np.ndarray, sr: int, out: dict[str, Any]) -> None:
    env, esr = _envelope(x, sr)
    n = env.size
    peak = float(env.max()) if n else 0.0
    if n < 2 or peak <= NOISE_FLOOR:
        return
    # LogAttackTime
    start = int(np.argmax(env >= 0.2 * peak))
    stop = int(np.argmax(env >= 0.9 * peak))
    attack = max((stop - start) / esr, 1.0 / sr)
    out["log_attack_time"] = math.log10(attack)
    out["amplitude_peak_ratio"] = float(np.argmax(env)) / n
    idx = np.arange(n, dtype=np.float64)
    total = float(env.sum())
    out["temporal_centroid_ratio"] = float((idx * env).sum() / total) / n
    # Centroid / CentralMoments / Decrease with Essentia's default range = 1
    axis = idx / (n - 1)
    c, spread, skew = _central_shape(env, axis)
    out["temporal_centroid"] = c
    out["temporal_spread"] = spread
    out["temporal_skewness"] = skew
    xm = axis.mean()
    var = float(((axis - xm) ** 2).sum())
    out["temporal_decrease"] = float(((axis - xm) * (env - env.mean())).sum() / var) if var > 0 else 0.0
    # EffectiveDuration (thresholdRatio 0.1 on the envelope)
    thr = max(0.1 * peak, NOISE_FLOOR)
    out["duration_effective"] = float(np.count_nonzero(env >= thr)) / esr
    # StrongDecay on the envelope: sqrt(energy / temporal centroid [s])
    centroid_s = float((idx * env).sum() / total) / esr
    energy = float((env * env).sum()) * ENV_BLOCK
    out["decay_strength"] = math.sqrt(energy / centroid_s) if centroid_s > 0 else 0.0


def _start_time(x: np.ndarray, sr: int) -> float | None:
    if x.size == 0:
        return None
    frames = _frames(x, FRAME, HOP)
    power = np.mean(frames.astype(np.float64) ** 2, axis=1)
    loud = np.flatnonzero(power > START_POWER)
    if loud.size == 0:
        return None
    return float(loud[0] * HOP) / sr


def _stage_spectral(x: np.ndarray, sr: int, out: dict[str, Any], cache: dict[str, Any]) -> None:
    frames = _frames(x, FRAME, HOP).astype(np.float64)
    power = np.mean(frames ** 2, axis=1)
    out["silence_rate"] = float(np.mean(power < SILENCE_POWER))
    active = power >= ACTIVE_POWER
    cache["frames"] = frames
    cache["active"] = active
    if not np.any(active):
        return
    fa = frames[active]
    mag = _magnitudes(fa)
    cache["mag"] = mag
    freqs = np.fft.rfftfreq(FRAME, 1.0 / sr)
    cache["freqs"] = freqs
    s_sum = mag.sum(axis=1)
    ok = s_sum > 0
    if not np.any(ok):
        return
    S = mag[ok]
    ssum = s_sum[ok][:, None]
    p = S / ssum
    centroid = p @ freqs
    d = freqs[None, :] - centroid[:, None]
    m2 = np.sum(p * d * d, axis=1)
    m3 = np.sum(p * d ** 3, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        skew = np.where(m2 > 0, m3 / np.power(np.maximum(m2, _EPS), 1.5), 0.0)
    out["spectral_centroid"] = float(centroid.mean())
    out["spectral_spread"] = float(m2.mean())
    out["spectral_skewness"] = float(skew.mean())
    pw = S * S
    cum = np.cumsum(pw, axis=1)
    roll_idx = np.argmax(cum >= 0.85 * cum[:, -1:], axis=1)
    out["spectral_rolloff"] = float(freqs[roll_idx].mean())
    geo = np.exp(np.mean(np.log(S + _EPS), axis=1))
    out["spectral_flatness"] = float(np.clip(geo / S.mean(axis=1), 0.0, 1.0).mean())
    out["spectral_crest"] = float((S.max(axis=1) / S.mean(axis=1)).mean())
    out["spectral_energy"] = float(pw.sum(axis=1).mean())
    with np.errstate(divide="ignore", invalid="ignore"):
        ent = -np.sum(np.where(p > 0, p * np.log2(np.maximum(p, _EPS)), 0.0), axis=1)
    out["spectral_entropy"] = float(ent.mean())
    # SpectralComplexity: peaks in 100..5000 Hz above 0.005
    band = (freqs >= 100) & (freqs <= 5000)
    inner = S[:, 1:-1]
    is_peak = (inner > S[:, :-2]) & (inner >= S[:, 2:]) & (inner > 0.005) & band[None, 1:-1]
    out["spectral_complexity"] = float(np.minimum(is_peak.sum(axis=1), 100).mean())
    # zero-crossing rate on the raw frames
    signs = np.signbit(fa)
    out["zero_crossing_rate"] = float(np.mean(np.count_nonzero(signs[:, 1:] != signs[:, :-1], axis=1) / FRAME))
    # PitchSalience: autocorrelation of the magnitude spectrum, lags 100..5000 Hz
    nfft = 1 << int(math.ceil(math.log2(2 * S.shape[1])))
    spec = np.fft.rfft(S, n=nfft, axis=1)
    ac = np.fft.irfft(np.abs(spec) ** 2, n=nfft, axis=1)[:, : S.shape[1]]
    bin_hz = sr / FRAME
    lo, hi = max(1, int(round(100 / bin_hz))), min(S.shape[1] - 1, int(round(5000 / bin_hz)))
    with np.errstate(divide="ignore", invalid="ignore"):
        sal = np.where(ac[:, 0] > 0, ac[:, lo: hi + 1].max(axis=1) / ac[:, 0], 0.0)
    out["pitch_salience"] = float(np.clip(sal, 0.0, 1.0).mean())


def _yin(frames: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """Vectorised YIN on ``frames`` (n, FRAME) -> (f0 Hz, confidence 0..1)."""
    n, size = frames.shape
    w = size // 2
    tau_min = max(2, int(sr / PITCH_FMAX))
    tau_max = min(w - 1, int(sr / PITCH_FMIN))
    nfft = 1 << int(math.ceil(math.log2(size + w)))
    a = np.fft.rfft(frames[:, :w], n=nfft, axis=1)
    b = np.fft.rfft(frames, n=nfft, axis=1)
    r = np.fft.irfft(np.conj(a) * b, n=nfft, axis=1)[:, : tau_max + 2]
    sq = np.concatenate([np.zeros((n, 1)), np.cumsum(frames * frames, axis=1)], axis=1)
    e0 = sq[:, w][:, None]
    taus = np.arange(tau_max + 2)
    et = sq[:, taus + w] - sq[:, taus]
    d = np.maximum(e0 + et - 2.0 * r, 0.0)
    d[:, 0] = 0.0
    cums = np.cumsum(d[:, 1:], axis=1)
    cmnd = np.ones_like(d)
    with np.errstate(divide="ignore", invalid="ignore"):
        cmnd[:, 1:] = np.where(cums > 0, d[:, 1:] * np.arange(1, d.shape[1]) / cums, 1.0)
    f0 = np.zeros(n)
    conf = np.zeros(n)
    seg = cmnd[:, tau_min: tau_max + 1]
    for i in range(n):
        row = seg[i]
        below = np.flatnonzero(row < 0.15)
        if below.size:
            j = int(below[0])
            while j + 1 < row.size and row[j + 1] < row[j]:
                j += 1
        else:
            j = int(np.argmin(row))
        tau = j + tau_min
        # parabolic interpolation on the cmnd dip
        if 1 <= tau < cmnd.shape[1] - 1:
            y0, y1, y2 = cmnd[i, tau - 1], cmnd[i, tau], cmnd[i, tau + 1]
            den = y0 - 2 * y1 + y2
            shift = 0.5 * (y0 - y2) / den if abs(den) > _EPS else 0.0
            shift = float(np.clip(shift, -0.5, 0.5))
        else:
            y1, shift = cmnd[i, tau], 0.0
        f0[i] = sr / (tau + shift)
        conf[i] = float(np.clip(1.0 - y1, 0.0, 1.0))
    return f0, conf


def _midi_from_hz(f: float) -> int:
    return int(round(69 + 12 * math.log2(f / 440.0)))


def _note_name(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def _stage_pitch(x: np.ndarray, sr: int, out: dict[str, Any], cache: dict[str, Any]) -> None:
    frames = cache.get("frames")
    active = cache.get("active")
    if frames is None or active is None or not np.any(active):
        return
    fa = frames[active]
    f0, conf = _yin(fa, sr)
    cache["f0"], cache["conf"] = f0, conf
    out["pitch_confidence"] = float(conf.mean())
    voiced = conf >= VOICED_CONFIDENCE
    sel = f0[voiced] if np.any(voiced) else f0
    out["pitch"] = float(sel.mean())
    out["pitch_min"] = float(sel.min())
    out["pitch_max"] = float(sel.max())
    out["pitch_var"] = float(sel.var())
    med = float(np.median(sel))
    if med > 0:
        midi = int(np.clip(_midi_from_hz(med), 0, 127))
        out["note_midi"] = midi
        out["note_name"] = _note_name(midi)
        out["note_confidence"] = float(np.median(conf))


def _even_subset(n: int, limit: int) -> np.ndarray:
    if n <= limit:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, limit).round().astype(int))


def _stage_harmonic(sr: int, out: dict[str, Any], cache: dict[str, Any]) -> None:
    mag = cache.get("mag")
    freqs = cache.get("freqs")
    if mag is None or freqs is None or mag.shape[0] == 0:
        return
    f0 = cache.get("f0")
    diss: list[float] = []
    inh: list[float] = []
    for i in _even_subset(mag.shape[0], MAX_TONAL_FRAMES):
        pf, pm = _spectral_peaks(mag[i], freqs, 20.0, 5000.0, 1e-5)
        if pf.size:
            diss.append(_dissonance(pf, pm))
        if f0 is not None and pf.size:
            hf, hm = _harmonic_peaks(pf, pm, float(f0[i]))
            v = _inharmonicity(hf, hm)
            if math.isfinite(v):
                inh.append(v)
    if diss:
        out["dissonance"] = float(np.mean(diss))
    if inh:
        out["inharmonicity"] = float(np.clip(np.mean(inh), 0.0, 1.0))


_KK_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KK_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def _hpcp(pf: np.ndarray, pm: np.ndarray, size: int = 36) -> np.ndarray:
    """36-bin HPCP (bin 0 = A440), squared-cosine weighting of 1 semitone, unit max."""
    h = np.zeros(size)
    if pf.size == 0:
        return h
    per_semi = size / 12
    pos = (size * np.log2(pf / 440.0)) % size
    half = per_semi / 2.0
    for p, mg in zip(pos, pm):
        lo = int(math.floor(p - half))
        for b in range(lo, int(math.ceil(p + half)) + 1):
            dist = abs(b - p)
            if dist <= half:
                h[b % size] += (math.cos(math.pi * dist / per_semi) ** 2) * mg * mg
    mx = h.max()
    return h / mx if mx > 0 else h


def _stage_tonal(x: np.ndarray, sr: int, out: dict[str, Any]) -> None:
    frames = _frames(x, TONAL_FRAME, TONAL_HOP).astype(np.float64)
    power = np.mean(frames ** 2, axis=1)
    frames = frames[power >= ACTIVE_POWER]
    if frames.shape[0] == 0:
        return
    sub = _even_subset(frames.shape[0], MAX_TONAL_FRAMES)
    mag = _magnitudes(frames[sub])
    freqs = np.fft.rfftfreq(TONAL_FRAME, 1.0 / sr)
    hp: list[np.ndarray] = []
    crest: list[float] = []
    ent: list[float] = []
    for row in mag:
        pf, pm = _spectral_peaks(row, freqs, 40.0, 5000.0, 1e-5)
        h = _hpcp(pf, pm)
        s = h.sum()
        if s <= 0:
            continue
        hp.append(h)
        crest.append(float(h.max() / h.mean()))
        q = h / s
        ent.append(float(-np.sum(q[q > 0] * np.log2(q[q > 0]))))
    if not hp:
        return
    out["hpcp_crest"] = float(np.mean(crest))
    out["hpcp_entropy"] = float(np.mean(ent))
    mean = np.mean(hp, axis=0)
    # fold 36 -> 12 (bin 3k is the centre of semitone k above A), then rotate so index 0 = C
    chroma_a = np.array([mean[(3 * k - 1) % 36] + mean[3 * k] + mean[3 * k + 1] for k in range(12)])
    chroma = np.roll(chroma_a, -3)  # C is 3 semitones above A
    if np.allclose(chroma, chroma[0]):
        return
    best = (-2.0, 0, "major")
    for tonic in range(12):
        rolled = np.roll(chroma, -tonic)
        for scale, prof in (("major", _KK_MAJOR), ("minor", _KK_MINOR)):
            r = float(np.corrcoef(rolled, prof)[0, 1])
            if math.isfinite(r) and r > best[0]:
                best = (r, tonic, scale)
    out["tonality"] = f"{KEY_NAMES[best[1]]} {best[2]}"
    out["tonality_confidence"] = float(max(0.0, best[0]))


def _k_weight(x: np.ndarray, sr: int) -> np.ndarray:
    """ITU-R BS.1770 K-weighting (pyloudnorm's RBJ biquads: shelf +4 dB @1500 Hz, HP @38 Hz)."""
    from scipy.signal import lfilter

    def shelf(G: float, Q: float, fc: float):
        A = 10 ** (G / 40.0)
        w0 = 2 * math.pi * fc / sr
        al = math.sin(w0) / (2 * Q)
        cw, sa = math.cos(w0), 2 * math.sqrt(A) * al
        b = [A * ((A + 1) + (A - 1) * cw + sa), -2 * A * ((A - 1) + (A + 1) * cw), A * ((A + 1) + (A - 1) * cw - sa)]
        a = [(A + 1) - (A - 1) * cw + sa, 2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - sa]
        return b, a

    def highpass(Q: float, fc: float):
        w0 = 2 * math.pi * fc / sr
        al = math.sin(w0) / (2 * Q)
        cw = math.cos(w0)
        return [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2], [1 + al, -2 * cw, 1 - al]

    y = x.astype(np.float64)
    for b, a in (shelf(4.0, 1 / math.sqrt(2), 1500.0), highpass(0.5, 38.0)):
        y = lfilter(b, a, y)
    return y


def _block_ms(y: np.ndarray, size: int, step: int) -> np.ndarray:
    c = np.concatenate([[0.0], np.cumsum(y * y)])
    starts = np.arange(0, y.size - size + 1, step)
    return (c[starts + size] - c[starts]) / size


def _lufs(ms: np.ndarray | float) -> np.ndarray:
    return -0.691 + 10.0 * np.log10(np.maximum(ms, _EPS))


def _stage_loudness(x: np.ndarray, sr: int, out: dict[str, Any]) -> None:
    if x.size < 16:
        return
    y = _k_weight(x, sr)
    size = min(int(round(0.4 * sr)), y.size)
    step = max(1, int(round(size * 0.25)))
    z = _block_ms(y, size, step)
    if z.size == 0:
        return
    lz = _lufs(z)
    gated = z[lz >= -70.0]
    if gated.size:
        rel = _lufs(gated.mean()) - 10.0
        g2 = z[(lz >= -70.0) & (lz >= rel)]
        if g2.size:
            out["loudness"] = float(_lufs(g2.mean()))
    # loudness range (EBU Tech 3342)
    st_size = int(round(3.0 * sr))
    if y.size < st_size:
        out["dynamic_range"] = 0.0 if gated.size else None
        return
    st = _lufs(_block_ms(y, st_size, max(1, int(round(0.1 * sr)))))
    st = st[st >= -70.0]
    if st.size == 0:
        out["dynamic_range"] = None
        return
    rel = float(_lufs(np.mean(10 ** ((st + 0.691) / 10.0)))) - 20.0
    st = st[st >= rel]
    if st.size < 2:
        out["dynamic_range"] = 0.0
        return
    out["dynamic_range"] = float(np.percentile(st, 95) - np.percentile(st, 10))


def _count_events(x: np.ndarray, sr: int, region_energy_thr: float = 0.5, silence_thr_scale: float = 4.5,
                  group_regions_ms: float = 50.0) -> int:
    """AudioCommons ``estimate_number_of_events`` (activity regions of a 15/50 ms envelope)."""
    env, esr = _envelope(x, sr, attack_ms=15.0, release_ms=50.0)
    if env.size == 0:
        return 0
    silence_thr = float(np.sum(env ** 2) / env.size) * silence_thr_scale
    mask = np.concatenate(([False], env > silence_thr, [False]))
    idx = np.flatnonzero(mask[1:] != mask[:-1])
    regions: list[tuple[float, float, float]] = []
    for i in range(0, idx.size - 1, 2):
        s, e = int(idx[i]), int(idx[i + 1])
        energy = float(np.sum(env[s:e] ** 2)) * ENV_BLOCK
        if energy > region_energy_thr:
            regions.append((s / esr, e / esr, energy))
    if len(regions) <= 1:
        return len(regions)
    grouped = [regions[0]]
    for r in regions[1:]:
        last = grouped[-1]
        if r[0] - last[1] < group_regions_ms / 1000.0:
            grouped[-1] = (last[0], r[1], last[2] + r[2])
        else:
            grouped.append(r)
    return len(grouped)


def loop_bpm_confidence(x: np.ndarray, sr: int, bpm: float) -> float:
    """Essentia LoopBpmConfidence: how well the (trimmed) duration fits whole beats at ``bpm``."""
    bpm = round(float(bpm))
    if bpm <= 0 or x.size == 0:
        return 0.0
    env, _ = _envelope(x, sr, attack_ms=10.0, release_ms=10.0)
    if env.size == 0 or env.max() <= 0:
        return 0.0
    above = np.flatnonzero(env >= 0.05 * env.max())
    start, end = int(above[0]) * ENV_BLOCK, (int(above[-1]) + 1) * ENV_BLOCK
    n = x.size
    beat = 60.0 * sr / bpm
    best = 0.0
    for length in (n, n - start, end, end - start):
        if length <= 0:
            continue
        k = max(1, round(length / beat))
        dist = min(abs(length - j * beat) for j in (k - 1, k, k + 1) if j >= 1)
        lam = 0.5 * beat
        best = max(best, 0.0 if dist > lam else 1.0 - dist / lam)
    return float(best)


def _stage_rhythm(x: np.ndarray, sr: int, out: dict[str, Any], truncated: bool) -> None:
    import librosa

    hop = 512
    duration = x.size / sr
    try:
        out["single_event"] = bool(duration <= 7.0 and _count_events(x, sr) == 1)
    except Exception as exc:  # noqa: BLE001
        log.debug("single_event failed: %s", exc)
    if x.size < 2048:
        out["onset_count"] = 0
        out["beat_count"] = 0
        out["loopable"] = False if not truncated else None
        return
    onset_env = librosa.onset.onset_strength(y=x, sr=sr, hop_length=hop)
    # un-normalised envelope (dB flux) + absolute floor so steady tones do not produce spurious onsets
    delta = max(0.1 * float(onset_env.max()) if onset_env.size else 0.0, 1.0)
    onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, hop_length=hop, units="frames",
                                        normalize=False, delta=delta)
    out["onset_count"] = int(len(onsets))
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, hop_length=hop, units="samples")
    beats = np.asarray(beats, dtype=np.int64)
    out["beat_count"] = int(beats.size)
    tempo_f = float(np.atleast_1d(tempo)[0]) if np.size(tempo) else 0.0
    if beats.size >= 2 and tempo_f > 0:
        out["bpm"] = int(round(tempo_f))
        e = onset_env - onset_env.mean()
        ac = np.correlate(e, e, mode="full")[e.size - 1:]
        lag = 60.0 / tempo_f * sr / hop
        li = int(round(lag))
        if ac[0] > 0 and 0 < li < ac.size:
            lo, hi = max(1, li - 1), min(ac.size, li + 2)
            out["bpm_confidence"] = float(np.clip(ac[lo:hi].max() / ac[0], 0.0, 1.0))
        else:
            out["bpm_confidence"] = 0.0
        half = int(0.05 * sr)
        loud = [float(np.mean(x[max(0, b - half): b + half].astype(np.float64) ** 2)) for b in beats]
        out["beat_loudness"] = float(np.mean(loud)) if loud else None
    else:
        out["bpm_confidence"] = 0.0
    if truncated:
        out["loopable"] = None
    else:
        bpm = out.get("bpm") or 0
        conf = loop_bpm_confidence(x, sr, bpm) if bpm else 0.0
        out["loopable"] = bool(conf > 0.95 and (out.get("onset_count") or 0) >= 2 and not out.get("single_event"))


# =========================================================================== entry points
_STAGES = ("temporal", "spectral", "pitch", "harmonic", "tonal", "loudness", "rhythm", "timbral")


def _run(name: str, func: Callable[[], None], keys: Iterable[str], out: dict[str, Any],
         timings: dict[str, float] | None) -> None:
    t0 = time.perf_counter()
    try:
        func()
    except CancelledError:
        raise
    except Exception as exc:  # one failing group must not kill the rest
        log.warning("descriptor stage %s failed: %s", name, exc, exc_info=log.isEnabledFor(logging.DEBUG))
        for k in keys:
            out.setdefault(k, None)
    finally:
        if timings is not None:
            timings[name] = timings.get(name, 0.0) + (time.perf_counter() - t0)


def extract_from_signal(
    samples: np.ndarray,
    sr: int,
    *,
    timbral: bool = True,
    ctx: JobContext | None = None,
    timings: dict[str, float] | None = None,
    truncated: bool = False,
    start_window: np.ndarray | None = None,
) -> dict[str, DescriptorValue]:
    """Descriptors of a mono float signal (see module docstring).

    ``truncated`` marks ``samples`` as a window of a longer file (``loopable`` ->
    ``None``); ``start_window`` (the beginning of that file) is then used for
    ``start_time``.
    """
    x = np.asarray(samples, dtype=np.float64).reshape(-1)
    x = np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)
    sr = int(sr)
    out: dict[str, Any] = {}
    total = len(_STAGES)

    def step(i: int, name: str) -> None:
        if ctx is not None:
            ctx.check()
            ctx.progress(i, total, name)

    if x.size == 0 or float(np.max(np.abs(x))) <= NOISE_FLOOR:
        result = _empty_result()
        result.update({"silence_rate": 1.0, "onset_count": 0, "beat_count": 0,
                       "single_event": False, "loopable": None if truncated else False})
        if ctx is not None:
            ctx.progress(total, total, "done")
        return result

    cache: dict[str, Any] = {}
    step(0, "temporal")
    _run("temporal", lambda: _stage_temporal(x, sr, out), TEMPORAL_DESCRIPTORS, out, timings)
    _run("temporal", lambda: out.__setitem__(
        "start_time", _start_time(start_window if start_window is not None else x, sr)), ("start_time",), out, timings)
    step(1, "spectral")
    _run("spectral", lambda: _stage_spectral(x, sr, out, cache),
         SPECTRAL_DESCRIPTORS + ("silence_rate", "pitch_salience"), out, timings)
    step(2, "pitch")
    _run("pitch", lambda: _stage_pitch(x, sr, out, cache),
         ("pitch", "pitch_min", "pitch_max", "pitch_var", "pitch_confidence", "note_name", "note_midi",
          "note_confidence"), out, timings)
    step(3, "harmonic")
    _run("harmonic", lambda: _stage_harmonic(sr, out, cache), ("dissonance", "inharmonicity"), out, timings)
    step(4, "tonal")
    _run("tonal", lambda: _stage_tonal(x, sr, out),
         ("hpcp_crest", "hpcp_entropy", "tonality", "tonality_confidence"), out, timings)
    step(5, "loudness")
    _run("loudness", lambda: _stage_loudness(x, sr, out), ("loudness", "dynamic_range"), out, timings)
    step(6, "rhythm")
    _run("rhythm", lambda: _stage_rhythm(x.astype(np.float32), sr, out, truncated), RHYTHM_DESCRIPTORS, out, timings)
    step(7, "timbral")
    if timbral:
        def _timbral() -> None:
            from musicplayer.core._timbral_compat import timbral_features

            seg = x
            limit = int(TIMBRAL_MAX_SECONDS * sr)
            if seg.size > limit:
                start = (seg.size - limit) // 2
                seg = seg[start: start + limit]
            out.update(timbral_features(seg, sr))

        _run("timbral", _timbral, TIMBRAL_DESCRIPTORS, out, timings)
    if ctx is not None:
        ctx.check()
        ctx.progress(total, total, "done")

    result = _empty_result()
    for name in DESCRIPTOR_NAMES:
        result[name] = _clean(out.get(name))
    for name in INT_DESCRIPTORS:
        if result[name] is not None and not isinstance(result[name], bool):
            result[name] = int(round(float(result[name])))  # type: ignore[arg-type]
    return result


def extract_descriptors(
    path: str | Path,
    *,
    max_seconds: float = 30.0,
    sr: int = 44100,
    timbral: bool = True,
    ctx: JobContext | None = None,
    timings: dict[str, float] | None = None,
) -> dict[str, DescriptorValue]:
    """Decode ``path`` (centre window of ``max_seconds``) and compute every descriptor."""
    from musicplayer.core.audio import load_mono

    t0 = time.perf_counter()
    truncated = False
    from musicplayer.core.audio import duration_seconds
    dur = duration_seconds(path)                        # libsndfile or the tags (.m4a); load_mono raises real errors
    if max_seconds and max_seconds > 0 and dur > 0:
        truncated = dur > max_seconds + 1e-6
    samples, rate = load_mono(path, target_sr=sr, max_seconds=max_seconds, offset_mode="center")
    start_window = None
    if truncated:
        start_window, _ = load_mono(path, target_sr=sr, max_seconds=max_seconds, offset_mode="start")
    if timings is not None:
        timings["decode"] = time.perf_counter() - t0
    return extract_from_signal(samples, rate, timbral=timbral, ctx=ctx, timings=timings,
                               truncated=truncated, start_window=start_window)


# =========================================================================== similarity
def descriptor_vector(desc: dict[str, Any], names: list[str] | tuple[str, ...], *,
                      log_scale: bool = True) -> np.ndarray:
    """Float64 vector of ``names`` from ``desc`` (bool -> 0/1; str, None, missing -> NaN).

    With ``log_scale`` descriptors whose catalog entry has ``"log": true``
    (heavy-tailed, e.g. frequencies, energy) are mapped to ``log10(max(v, 1e-12))``.
    """
    catalog = load_catalog() if log_scale else {}
    vec = np.full(len(names), np.nan, dtype=np.float64)
    for i, name in enumerate(names):
        v = desc.get(name) if desc else None
        if v is None or isinstance(v, str):
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(f):
            continue
        if log_scale and (catalog.get(name) or {}).get("log"):
            f = math.log10(max(f, 1e-12))
        vec[i] = f
    return vec


def standardize(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """NaN-aware column z-scores -> ``(z, mean, std)``; NaNs stay NaN, constant columns use std 1."""
    m = np.asarray(matrix, dtype=np.float64)
    if m.ndim == 1:
        m = m[None, :]
    with np.errstate(all="ignore"):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            mean = np.nanmean(m, axis=0) if m.shape[0] else np.zeros(m.shape[1])
            std = np.nanstd(m, axis=0) if m.shape[0] else np.ones(m.shape[1])
    mean = np.where(np.isfinite(mean), mean, 0.0)
    std = np.where(np.isfinite(std) & (std > 1e-12), std, 1.0)
    return (m - mean) / std, mean, std


def nearest(query_vec: np.ndarray, matrix: np.ndarray, k: int = 10, *,
            exclude: Iterable[int] | None = None) -> list[tuple[int, float]]:
    """Rows of ``matrix`` most similar to ``query_vec`` -> ``[(row, cosine), ...]`` best first.

    Both are standardized with the column statistics of ``matrix``; NaN entries
    become 0 (= column mean) so missing descriptors neither help nor hurt.
    """
    m = np.asarray(matrix, dtype=np.float64)
    if m.ndim != 2 or m.shape[0] == 0 or k <= 0:
        return []
    z, mean, std = standardize(m)
    q = (np.asarray(query_vec, dtype=np.float64).reshape(-1) - mean) / std
    z = np.nan_to_num(z, nan=0.0, posinf=0.0, neginf=0.0)
    q = np.nan_to_num(q, nan=0.0, posinf=0.0, neginf=0.0)
    qn = float(np.linalg.norm(q))
    rn = np.linalg.norm(z, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        sims = np.where((rn > 0) & (qn > 0), (z @ q) / (rn * qn if qn > 0 else 1.0), 0.0)
    sims = np.nan_to_num(sims, nan=0.0)
    skip = {int(i) for i in exclude} if exclude is not None else set()
    order = np.argsort(-sims, kind="stable")
    result: list[tuple[int, float]] = []
    for i in order:
        if int(i) in skip:
            continue
        result.append((int(i), float(sims[i])))
        if len(result) >= k:
            break
    return result

