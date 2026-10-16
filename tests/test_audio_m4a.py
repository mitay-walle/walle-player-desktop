"""AAC (.m4a) is not a libsndfile format: decoding falls back to ffmpeg (descriptors, waveform)."""

import subprocess

import numpy as np
import pytest
import soundfile as sf

from musicplayer.core.audio import duration_seconds, ffmpeg_exe, load_mono, waveform_peaks
from musicplayer.core.descriptors import extract_descriptors

pytestmark = pytest.mark.skipif(not ffmpeg_exe(), reason="ffmpeg is not installed")


@pytest.fixture
def m4a(tmp_path):
    sr = 44100
    t = np.arange(sr * 40) / sr
    x = (0.4 * np.sin(2 * np.pi * 220 * t) * (np.sin(2 * np.pi * 2 * t) > 0)).astype(np.float32)
    wav, out = tmp_path / "a.wav", tmp_path / "a.m4a"
    sf.write(wav, x, sr)
    subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-i", str(wav), "-c:a", "aac", "-b:a", "128k", str(out)],
                   check=True)
    return out


def test_m4a_decodes_through_ffmpeg(m4a):
    with pytest.raises(sf.LibsndfileError):
        sf.info(str(m4a))
    assert 39 < duration_seconds(m4a) < 41
    x, sr = load_mono(m4a, target_sr=22050, max_seconds=10, offset_mode="center")
    assert sr == 22050 and abs(len(x) - 10 * sr) < sr * 0.1 and 0.2 < np.abs(x).max() <= 1.0
    peaks = waveform_peaks(m4a, 50)
    assert peaks.shape == (50, 2) and peaks.min() >= -1 and peaks.max() <= 1
    d = extract_descriptors(m4a, max_seconds=30, timbral=False)
    assert d["loudness"] is not None and d["duration_effective"] > 0
