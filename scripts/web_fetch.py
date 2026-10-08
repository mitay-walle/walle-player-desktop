"""Fetch internet tags/moods into a catalogue: python scripts/web_fetch.py <db> [labeled|all] (shows progress)."""
import sys, time
sys.path.insert(0, ".")
from musicplayer.core.db import Database
from musicplayer.core import webtags
from musicplayer.core.jobs import JobContext

db = Database(sys.argv[1])
scope = sys.argv[2] if len(sys.argv) > 2 else "labeled"
ids = [t for t, _ in db.mood_training_set()] if scope == "labeled" else None
t0 = time.time()
last = [0.0]
def prog(d, t, m):
    if time.time() - last[0] > 20 or d == t:
        last[0] = time.time()
        print(f"[{int(time.time() - t0)}s] {m}", flush=True)
ctx = JobContext(prog, lambda m: print(m, flush=True))
print(webtags.fetch_tracks(db, ids, ctx))
print(webtags.agreement(db))
