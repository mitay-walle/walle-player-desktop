import numpy as np

from musicplayer.core.spectrum import SpectrumAnalyzer, to_mono_float

SR = 44100


def tone(freq, n=4096):
    return (0.8 * np.sin(2 * np.pi * freq * np.arange(n) / SR)).astype(np.float32)


def run(an, sig, frames=30):
    for _ in range(frames):
        an.feed(sig, SR)
        an.step(True)


def test_bass_tone_lights_low_bands_and_treble_tone_high_bands():
    low, high = SpectrumAnalyzer(), SpectrumAnalyzer()
    run(low, tone(80))
    run(high, tone(9000))
    assert low.bass > 0.4 and low.bass > 3 * low.treble
    assert high.treble > 0.3 and high.treble > 3 * high.bass
    assert int(np.argmax(low.levels)) < 12 and int(np.argmax(high.levels)) > 30


def test_silence_decays_and_pause_decays():
    an = SpectrumAnalyzer()
    run(an, tone(440))
    assert an.levels.max() > 0.4
    for _ in range(60):
        an.step(False)
    assert an.levels.max() < 0.02


def test_beat_flag_on_bass_burst():
    an = SpectrumAnalyzer()
    run(an, np.zeros(4096, dtype=np.float32), 10)
    an.feed(tone(60), SR)
    an.step(True)
    assert an.beat == 1.0


def test_pcm_decoding():
    i16 = (np.array([[16384, -16384]] * 4, dtype=np.int16)).tobytes()
    m = to_mono_float(i16, "i16", 2)
    assert m.shape == (4,) and np.allclose(m, 0.0)
    f = to_mono_float(np.array([0.5, 0.5], dtype=np.float32).tobytes(), "f32", 1)
    assert np.allclose(f, 0.5)
