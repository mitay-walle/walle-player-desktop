import random
import time

from musicplayer.core.db import weighted_order
from musicplayer.core.plays import PlayTracker


def run(duration, positions, seeks=()):
    counted = []
    t = PlayTracker(lambda tid, sec: counted.append((tid, sec)))
    t.start(1, duration)
    for i, p in enumerate(positions):
        if i in seeks:
            t.seek()
        t.update(p)
    return counted


def test_counts_after_half_of_short_track():
    assert run(100, range(0, 60)) == [(1, 51.0)] or len(run(100, range(0, 60))) == 1


def test_not_counted_before_half():
    assert run(100, range(0, 50)) == []


def test_counted_once():
    assert len(run(100, range(0, 100))) == 1


def test_long_track_counts_at_four_minutes():
    assert run(1200, range(0, 239)) == []
    assert len(run(1200, range(0, 245))) == 1


def test_seek_forward_is_not_listening():
    # jump from 10 s to 90 s must not add 80 s
    assert run(100, [0, 1, 2, 3, 90, 91, 92]) == []


def test_pause_does_not_accumulate():
    c = []
    t = PlayTracker(lambda *a: c.append(a))
    t.start(1, 10)
    for p in range(0, 4):
        t.update(p)
    for _ in range(100):
        t.update(3, playing=False)
    assert c == []


def test_replay_counts_again():
    c = []
    t = PlayTracker(lambda *a: c.append(a))
    for _ in range(2):
        t.start(5, 10)
        for p in range(0, 9):
            t.update(p)
    assert len(c) == 2


def test_plays_db_and_periods(db):
    from musicplayer.core.db import Database
    tid = db.upsert_track({"path": "a.mp3", "title": "a", "artist": "x"}, [])
    now = time.time()
    db.record_play(tid, 100, when=now - 3600)
    db.record_play(tid, 100, when=now - 40 * 86400)
    db.record_play(tid, 100, when=now - 400 * 86400)
    t = db.get_track(tid)
    assert t["play_count"] == 3
    assert t["plays_by_period"]["week"] == 1
    assert t["plays_by_period"]["month"] == 1
    assert t["plays_by_period"]["year"] == 2
    assert [r["id"] for r in db.search(min_plays=2, plays_days=365)] == [tid]
    assert db.search(min_plays=2, plays_days=30) == []
    assert db.stats(period_days=30)["most_played"][0]["plays"] == 1
    assert db.stats()["most_played"][0]["plays"] == 3


def test_rare_random_prefers_unplayed(db):
    ids = []
    for i in range(20):
        ids.append(db.upsert_track({"path": f"{i}.mp3", "title": str(i), "artist": "x"}, []))
    for tid in ids[:10]:
        for _ in range(30):
            db.record_play(tid)
    firsts = 0
    for _ in range(100):
        r = db.search(order="rare_random", limit=1)[0]
        firsts += r["play_count"] == 0
    assert firsts > 85  # 1/31 weight vs 1: unplayed nearly always wins


def test_weighted_order_is_permutation():
    rows = [{"id": i, "play_count": i} for i in range(10)]
    out = weighted_order(rows, rng=random.Random(1))
    assert sorted(r["id"] for r in out) == list(range(10))
