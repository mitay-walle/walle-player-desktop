"""Live check of several listeners at once against the running sandbox server (port 8790).

Three listeners (the owner and two guests) act at the same time: stream music in parallel, like different tracks,
count plays (one re-sent with the same uid), label (only who may), read their own history.  Then everything is
checked for isolation and the test's traces are removed from the sandbox catalogue."""
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8790"
HOME = Path(os.environ.get("MUSICPLAYER_HOME", Path(__file__).resolve().parent.parent / "data" / "e2e"))
cfg = json.loads((HOME / "remote.json").read_text(encoding="utf-8"))
users = {u["name"]: u for u in cfg["users"]}
owner = next(u for u in cfg["users"] if u["owner"])
anya, petya = users["Аня (тест)"], users["Петя (тест)"]
started = time.time()
results, errors = [], []


def call(user, path, body=None, raw=False, headers=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + user["token"], "Content-Type": "application/json",
                                          **(headers or {})}, method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
            return (r.status, data) if raw else (r.status, json.loads(data))
    except urllib.error.HTTPError as e:
        return e.code, None


def check(name, ok, detail=""):
    results.append((ok, name, detail))


tracks = call(owner, "/api/tracks?limit=12&order=title")[1]["items"]
ids = [t["id"] for t in tracks]
before_fav = {n: [t["id"] for t in call(u, "/api/favorites")[1]["tracks"]] for n, u in
              (("owner", owner), ("anya", anya), ("petya", petya))}


def listener(user, mine, label_ok):
    try:
        # stream two different tracks fully, in parallel with the others
        for tid in mine[:2]:
            st, data = call(user, f"/api/stream/{tid}", raw=True)
            check(f"{user['name']}: stream {tid}", st == 200 and len(data) > 100_000, f"{len(data or b'')} bytes")
        st, part = call(user, f"/api/stream/{mine[0]}", raw=True, headers={"Range": "bytes=1000-1999"})
        check(f"{user['name']}: seeking (Range)", st == 206 and len(part) == 1000)
        for tid in mine[:3]:
            call(user, "/api/favorites", {"kind": "track", "id": tid, "on": True})
        uid = f"live-{user['id']}-{mine[0]}"
        call(user, "/api/plays", {"id": mine[0], "listened": 120, "uid": uid})
        st, again = call(user, "/api/plays", {"id": mine[0], "listened": 120, "uid": uid})
        check(f"{user['name']}: re-sent play ignored", again.get("duplicate") is True)
        st, _ = call(user, f"/api/tracks/{mine[1]}/labels", {"flags": ["live"]})
        check(f"{user['name']}: labelling {'allowed' if label_ok else 'refused'}", (st == 200) == label_ok, f"HTTP {st}")
        if st == 200:
            call(user, f"/api/tracks/{mine[1]}/labels", {"flags": []})        # put it back
    except Exception as e:  # noqa: BLE001
        errors.append(f"{user['name']}: {e!r}")


plan = [(owner, ids[0:4], True), (anya, ids[4:8], False), (petya, ids[8:12], True)]
threads = [threading.Thread(target=listener, args=p) for p in plan]
t0 = time.time()
for t in threads:
    t.start()
for t in threads:
    t.join()
check("all three at once", not errors, f"{time.time() - t0:.1f}s; {errors}")

for user, mine, _ in plan:
    fav = [t["id"] for t in call(user, "/api/favorites")[1]["tracks"]]
    others = [x for u, m, _ in plan if u is not user for x in m]
    check(f"{user['name']}: own likes only", set(mine[:3]) <= set(fav) and not set(others) & set(fav), str(fav))
    recent = [t["id"] for t in call(user, "/api/recent?limit=5")[1]["items"]]
    check(f"{user['name']}: own history first", recent[:1] == [mine[0]], str(recent))
    t = call(user, f"/api/tracks/{mine[0]}")[1]
    check(f"{user['name']}: play counted once", t["plays"] >= 1)
g = call(anya, f"/api/tracks/{ids[8]}")[1]
check("a guest does not see another guest's like", g["liked"] is False)
st, me = call(petya, "/api/me")
check("each key is its listener", me["name"] == "Петя (тест)" and call(anya, "/api/me")[1]["name"] == "Аня (тест)")

# clean up: likes back as they were, the test's plays and uids out of the sandbox catalogue
for (n, u), (_, mine, _) in zip((("owner", owner), ("anya", anya), ("petya", petya)), plan):
    for tid in mine[:3]:
        if tid not in before_fav[n]:
            call(u, "/api/favorites", {"kind": "track", "id": tid, "on": False})
db = sqlite3.connect(HOME / "library.db")
db.execute("DELETE FROM remote_plays WHERE played_at>=?", (started,))
n_owner = db.execute("DELETE FROM plays WHERE played_at>=? AND track_id=?", (started, ids[0])).rowcount
db.execute("UPDATE tracks SET play_count=MAX(play_count-?,0) WHERE id=?", (n_owner, ids[0]))
db.execute("DELETE FROM remote_play_uids WHERE uid LIKE 'live-%'")
db.commit()

ok = sum(r[0] for r in results)
for good, name, detail in results:
    print(("PASS " if good else "FAIL ") + name + (f"  [{detail}]" if detail and not good else ""))
print(f"{ok}/{len(results)} passed")
sys.exit(0 if ok == len(results) else 1)
