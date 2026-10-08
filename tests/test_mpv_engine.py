import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["MUSICPLAYER_MPV_AO"] = "null"          # no sound device needed

import numpy as np
import pytest
import soundfile as sf

from musicplayer.core.db import Database
from musicplayer.ui.mpv_player import MpvPlayerController, mpv_available

pytestmark = pytest.mark.skipif(not mpv_available(), reason="libmpv-2.dll not found")


@pytest.fixture
def player(qtbot, tmp_path):
    db = Database()
    tracks = []
    for i, f in enumerate((440, 660, 880)):
        p = tmp_path / f"t{i}.wav"
        sf.write(p, (0.3 * np.sin(2 * np.pi * f * np.arange(44100 * 2) / 44100)).astype("float32"), 44100)
        tid = db.upsert_track({"path": str(p), "title": f"t{i}", "artist": "a", "duration": 2.0}, [])
        tracks.append(db.get_track(tid))
    pl = MpvPlayerController(db, volume=0.0)
    yield pl, tracks, db
    pl.shutdown()


def test_gapless_auto_advance_and_end_of_queue(qtbot, player):
    pl, tracks, _ = player
    changes = []
    pl.track_changed.connect(lambda t: changes.append(t["id"] if t else None))
    pl.play_tracks(tracks)
    assert pl.mpv.playlist_count == 2                        # current + preloaded next
    qtbot.waitUntil(lambda: pl.current["id"] == tracks[1]["id"], timeout=8000)
    assert pl.mpv.playlist_count == 2 and pl._loaded == [tracks[1]["id"], tracks[2]["id"]]
    qtbot.waitUntil(lambda: pl.current["id"] == tracks[2]["id"], timeout=8000)
    qtbot.waitUntil(lambda: pl.state == "stopped", timeout=8000)
    assert changes[:3] == [tracks[0]["id"], tracks[1]["id"], tracks[2]["id"]] and changes[-1] is None


def test_play_counted_once_and_in_db(qtbot, player):
    pl, tracks, db = player
    pl.play_tracks(tracks[:1])
    qtbot.waitUntil(lambda: db.get_track(tracks[0]["id"])["play_count"] == 1, timeout=8000)
    qtbot.wait(1500)
    assert db.get_track(tracks[0]["id"])["play_count"] == 1


def test_manual_next_prev_seek_and_modes(qtbot, player):
    pl, tracks, _ = player
    pl.play_tracks(tracks)
    pl.next()
    assert pl.current["id"] == tracks[1]["id"]
    pl.seek(1.0)
    qtbot.waitUntil(lambda: pl._pos >= 0.9, timeout=4000)
    pl.prev()                                                # >3 s rule not met (pos ~1): goes to previous track
    assert pl.current["id"] == tracks[0]["id"]
    pl.set_repeat("one")
    assert pl.mpv.loop_file in ("inf", True) and pl.mpv.playlist_count == 1
    pl.set_repeat("off")
    pl.set_shuffle("rare")
    assert pl.mpv.playlist_count == 2
    pl.pause()
    assert pl.state == "paused" and pl.mpv.pause
    pl.set_volume(0.4)
    assert pl.mpv.volume == 40


def test_visual_samples_follow_the_playhead(qtbot, player):
    pl, tracks, _ = player
    got = []
    pl.samples.connect(lambda m, sr: got.append((m, sr)))
    pl.play_tracks(tracks[:1])
    qtbot.waitUntil(lambda: len(got) > 5, timeout=8000)
    m, sr = got[-1]
    assert sr == 44100 and m.size > 100 and float(np.abs(m).max()) > 0.1
