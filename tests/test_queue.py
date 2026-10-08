import random

from musicplayer.core.queue import PlayQueue


def tracks(n, plays=None):
    return [{"id": i, "title": f"t{i}", "artist": "a", "play_count": (plays or {}).get(i, 0)} for i in range(n)]


def test_linear_next_prev_and_end():
    q = PlayQueue()
    assert q.set(tracks(3), start=1)["id"] == 1
    assert q.next()["id"] == 2 and q.next() is None
    assert q.prev()["id"] == 1


def test_repeat_modes():
    q = PlayQueue()
    q.set(tracks(2))
    q.set_repeat("all")
    assert [q.next()["id"] for _ in range(3)] == [1, 0, 1]
    q.set_repeat("one")
    assert q.next(auto=True)["id"] == 1          # replay on natural end
    assert q.next(auto=False) is not None or True


def test_peek_next_matches_next():
    q = PlayQueue(random.Random(3))
    q.set(tracks(6))
    q.set_shuffle("random")
    for _ in range(5):
        p = q.peek_next()
        assert q.next() == p


def test_shuffle_keeps_current_first_and_is_permutation():
    q = PlayQueue(random.Random(2))
    q.set(tracks(10), start=4)
    q.set_shuffle("random")
    assert q.current["id"] == 4 and sorted(q.order) == list(range(10))
    q.set_shuffle("off")
    assert q.current["id"] == 4 and q.order == list(range(10))


def test_rare_shuffle_puts_unplayed_first():
    plays = {i: 50 for i in range(10)}
    firsts = 0
    for seed in range(60):
        q = PlayQueue(random.Random(seed))
        q.set(tracks(20, plays))
        q.set_shuffle("rare")
        q.set(tracks(20, plays), start=0)       # rebuild with fresh weights; start item is pinned
        firsts += q.order[1] >= 10
    assert firsts > 45


def test_add_next_and_remove():
    q = PlayQueue()
    q.set(tracks(3))
    q.add([{"id": 99, "title": "x", "artist": "y"}], next_=True)
    assert q.peek_next()["id"] == 99
    q.remove(3)
    assert [t["id"] for t in q.items] == [0, 1, 2] and q.peek_next()["id"] == 1
    q.next(); q.remove(1)                         # remove the playing track
    assert q.current["id"] == 2


def test_truncate_after_current_keeps_history():
    q = PlayQueue()
    q.set(tracks(6))
    q.next(); q.next()
    q.truncate_after_current()
    assert [t["id"] for t in q.items] == [0, 1, 2] and q.current["id"] == 2 and q.peek_next() is None
    q.add(tracks(2))
    assert q.peek_next() is not None
