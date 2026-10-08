"""Real-time spectrum for the colour-music visualiser: log-spaced bands, bass/mid/treble, beat flash.  Qt-free."""

from __future__ import annotations

import numpy as np

N_BANDS = 48
FFT = 2048
F_MIN, F_MAX = 30.0, 16000.0


class SpectrumAnalyzer:
    def __init__(self, bands: int = N_BANDS, attack: float = 0.6, decay: float = 0.12) -> None:
        self.n = bands
        self.attack, self.decay = attack, decay
        self.buf = np.zeros(FFT, dtype=np.float32)
        self.sr = 44100
        self.levels = np.zeros(bands)
        self.bass = self.mid = self.treble = 0.0
        self.beat = 0.0                 # 1.0 on a detected beat, decays to 0
        self.sensitivity = 1.0
        self._bass_avg = 1e-4
        self._win = np.hanning(FFT).astype(np.float32)
        self.live = False               # True while real samples arrive

    def feed(self, mono: np.ndarray, sr: int) -> None:
        """Append mono float samples in [-1, 1]."""
        if mono.size == 0:
            return
        self.sr, self.live = sr, True
        m = mono[-FFT:]
        self.buf = np.concatenate([self.buf[m.size:], m.astype(np.float32)]) if m.size < FFT else m.copy()

    def _edges(self) -> np.ndarray:
        return np.geomspace(F_MIN, min(F_MAX, self.sr / 2 - 1), self.n + 1)

    def step(self, playing: bool = True) -> np.ndarray:
        """Advance one display frame (call ~30-60x/s) and return the smoothed band levels (0..1)."""
        if playing and self.live:
            spec = np.abs(np.fft.rfft(self.buf * self._win)) / (FFT / 4)
            freqs = np.fft.rfftfreq(FFT, 1 / self.sr)
            edges = self._edges()
            raw = np.zeros(self.n)
            for i in range(self.n):
                sel = (freqs >= edges[i]) & (freqs < edges[i + 1])
                if sel.any():
                    raw[i] = spec[sel].max()
                else:  # narrow low band between FFT bins: interpolate
                    raw[i] = np.interp((edges[i] + edges[i + 1]) / 2, freqs, spec)
            # dB-ish compression, tilt so highs are visible
            tilt = np.linspace(1.0, 2.6, self.n)
            target = np.clip((20 * np.log10(raw * tilt + 1e-6) + 62) / 62 * self.sensitivity, 0, 1)
        else:
            target = np.zeros(self.n)
            self.live = self.live and playing
        up = target > self.levels
        self.levels = np.where(up, self.levels + (target - self.levels) * self.attack,
                               self.levels + (target - self.levels) * self.decay)
        k = max(1, self.n // 6)
        third = self.n // 3
        zone = lambda a: float(0.5 * a.max() + 0.5 * a.mean())  # noqa: E731 - peaks and body both matter
        self.bass = zone(self.levels[:k * 2])
        self.mid = zone(self.levels[third:2 * third])
        self.treble = zone(self.levels[2 * third:])
        flux = max(0.0, self.bass - self._bass_avg)
        self._bass_avg = self._bass_avg * 0.9 + self.bass * 0.1
        if flux > 0.12 and playing:
            self.beat = 1.0
        else:
            self.beat *= 0.82
        return self.levels


def to_mono_float(raw: bytes, sample_format: str, channels: int) -> np.ndarray:
    """Decode interleaved PCM bytes into mono float32 in [-1, 1].  sample_format: u8|i16|i32|f32."""
    dt = {"u8": np.uint8, "i16": np.int16, "i32": np.int32, "f32": np.float32}[sample_format]
    a = np.frombuffer(raw, dtype=dt)
    if channels > 1:
        a = a[: a.size // channels * channels].reshape(-1, channels).mean(axis=1)
    a = a.astype(np.float32)
    if sample_format == "u8":
        return (a - 128.0) / 128.0
    if sample_format == "i16":
        return a / 32768.0
    if sample_format == "i32":
        return a / 2147483648.0
    return a
