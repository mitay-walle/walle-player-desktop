"""Sound quality check: re-encoded «320» from a poor copy, low bitrate, a cut download, a broken file."""
import subprocess

import numpy as np
import pytest

from musicplayer.core import quality
from musicplayer.core.audio import ffmpeg_exe


def noise(seconds: float, top_hz: float | None = None, sr: int = 44100) -> np.ndarray:
    x = np.random.default_rng(0).normal(0, 0.2, int(seconds * sr)).astype(np.float32)
    if top_hz:
        spec = np.fft.rfft(x)
        spec[np.fft.rfftfreq(x.size, 1 / sr) > top_hz] = 0
        x = np.fft.irfft(spec, x.size).astype(np.float32)
    return x


def test_cutoff_follows_the_highest_band_with_sound():
    assert quality.cutoff_hz(noise(4)) >= 21000
    assert 15500 <= quality.cutoff_hz(noise(4, 16000)) <= 16500
    assert 10500 <= quality.cutoff_hz(noise(4, 11000)) <= 11500


def test_judging():
    assert quality.judge({"bitrate": 320, "cutoff": 16250, "clip": 0})[0] == "fake"
    assert quality.judge({"bitrate": 320, "cutoff": 20000, "clip": 0})[0] == "ok"
    assert quality.judge({"bitrate": 128, "cutoff": 16000, "clip": 0})[0] == "low"
    assert quality.judge({"bitrate": 0, "cutoff": 0, "error": "ffmpeg: Invalid data"})[0] == "broken"
    assert quality.judge({"bitrate": 320, "cutoff": 20000, "cut": True})[0] == "cut"


@pytest.mark.skipif(not ffmpeg_exe(), reason="needs ffmpeg")
def test_check_finds_bad_files(db, tmp_path):
    exe = ffmpeg_exe()

    def mp3(name, rate, top=None, seconds=40):
        raw = tmp_path / f"{name}.f32"
        raw.write_bytes(noise(seconds, top).tobytes())
        out = tmp_path / f"{name}.mp3"
        subprocess.run([exe, "-v", "error", "-y", "-f", "f32le", "-ar", "44100", "-ac", "1", "-i", str(raw),
                        "-b:a", rate, str(out)], check=True)
        return out
    good, fake, low = mp3("good", "320k"), mp3("fake", "320k", 16000), mp3("low", "96k")
    cut = tmp_path / "cut.mp3"
    cut.write_bytes(good.read_bytes()[: good.stat().st_size // 3])      # the tags still say 40 s
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"not audio at all" * 1000)
    ids = {p.stem: db.upsert_track({"path": str(p), "title": p.stem, "artist": "a"}, [])
           for p in (good, fake, low, cut, broken)}
    assert len(quality.pending(db)) == 5
    got = quality.check(db)
    assert got["left"] == 0 and not quality.pending(db)
    verdict = {r["title"]: r["verdict"] for r in quality.listing(db, tuple(quality.VERDICTS))}
    assert verdict == {"good": "ok", "fake": "fake", "low": "low", "cut": "cut", "broken": "broken"}, verdict
    bad = [r["title"] for r in quality.listing(db)]
    assert bad[0] == "broken" and "good" not in bad
    assert quality.summary(db)["checked"] == 5
    assert quality.check(db)["left"] == 0 and ids                       # nothing is looked at twice


def test_window_shows_bad_files_marks_and_exports_them(qtbot, tmp_path, library, monkeypatch):
    import json
    import os
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from musicplayer.core import deletemarks
    from musicplayer.core.scanner import scan
    from musicplayer.ui import quality_dialog
    from musicplayer.ui.app import create_app
    _, ctx, win = create_app(home=tmp_path / "home", mcp=False)
    qtbot.addWidget(win)
    scan(ctx.db, library, tmp_path / "covers")
    rows = ctx.db.search(limit=3)
    quality.save(ctx.db, rows[0]["id"], {"bitrate": 320, "cutoff": 16000})
    quality.save(ctx.db, rows[1]["id"], {"bitrate": 320, "cutoff": 20000})
    ctx.db.conn.commit()
    win._update_marks()
    assert win.btn_quality.isVisibleTo(win) and "(1)" in win.btn_quality.text()
    d = quality_dialog.QualityDialog(ctx, win)
    assert [t["id"] for t in d.rows] == [rows[0]["id"]] and d.rows[0]["verdict"] == "fake"
    d.tree.topLevelItem(0).child(0).setSelected(True)
    d.mark()
    assert deletemarks.count(ctx.db) == 1
    out = tmp_path / "re.json"
    monkeypatch.setattr(quality_dialog.QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    d.export()
    got = json.loads(out.read_text(encoding="utf-8"))
    assert got["what"] == "redownload" and got["items"][0]["title"] == rows[0]["title"] and "16.0" in got["items"][0]["reason"]
    d.accept_as_is()
    assert not quality.listing(ctx.db)
