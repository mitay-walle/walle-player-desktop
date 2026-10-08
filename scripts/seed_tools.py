"""Bootstrap labelling helpers (Claude labels a representative sample, the model learns from it).

    python scripts/seed_tools.py select [N]        # pick N diverse tracks (k-means on CLAP embeddings) -> seed_ids.txt
    python scripts/seed_tools.py sheet <k> [size]  # print batch k of the sample as compact lines with every signal
    python scripts/seed_tools.py apply <file.json> # {"<track id>": ["Грустное", ...], ...} -> labels with source 'seed'
    python scripts/seed_tools.py stats             # label counts per mood
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from musicplayer.core import clap, webtags  # noqa: E402
from musicplayer.core.db import Database  # noqa: E402
from musicplayer.core.paths import data_dir  # noqa: E402

BOOT = ROOT / "data" / "bootstrap"
SEED_FILE = BOOT / "seed_ids.txt"
from musicplayer.core.moods import DEFAULT_MOODS as VOCAB  # noqa: E402


def open_db() -> tuple[Database, Path]:
    home = data_dir()
    return Database(home / "library.db"), home


def select(n: int) -> None:
    from sklearn.cluster import KMeans
    db, _ = open_db()
    rows = db.conn.execute("SELECT t.id, COALESCE(NULLIF(t.albumartist,''),t.artist) a, e.vec FROM tracks t "
                           "JOIN track_embeddings e ON e.track_id=t.id AND e.model='clap' WHERE t.missing=0").fetchall()
    ids = [r[0] for r in rows]
    artists = [(r[1] or "").casefold() for r in rows]
    E = np.vstack([np.frombuffer(r[2], dtype=np.float32) for r in rows])
    k = max(10, n // 10)
    km = KMeans(k, n_init=3, random_state=0).fit(E)
    rng = np.random.RandomState(0)
    chosen: list[int] = []
    per_artist: dict[str, int] = {}
    for c in range(k):                                    # round-robin over clusters: nearest to centre + random members
        idx = np.where(km.labels_ == c)[0]
        d = np.linalg.norm(E[idx] - km.cluster_centers_[c], axis=1)
        order = list(idx[np.argsort(d)][: n // k // 2 + 1]) + list(rng.permutation(idx)[: n // k])
        taken = 0
        for i in order:
            if ids[i] in chosen or per_artist.get(artists[i], 0) >= 5:
                continue
            chosen.append(ids[i])
            per_artist[artists[i]] = per_artist.get(artists[i], 0) + 1
            taken += 1
            if taken >= n // k:
                break
    rng.shuffle(chosen)
    SEED_FILE.parent.mkdir(parents=True, exist_ok=True)
    SEED_FILE.write_text("\n".join(map(str, chosen[:n])), encoding="utf-8")
    print(f"{len(chosen[:n])} tracks from {len(set(per_artist))} artists written to {SEED_FILE}")


def fmt_desc(d: dict | None) -> str:
    if not d:
        return "—"
    def g(k, f="{:.0f}"):
        v = d.get(k)
        return f.format(v) if isinstance(v, (int, float)) else "?"
    ton = str(d.get("tonality") or "")
    mode = "min" if "minor" in ton.lower() else "maj" if "major" in ton.lower() else "?"
    return (f"bpm {g('bpm')} loud {g('loudness')} dyn {g('dynamic_range')} cent {g('spectral_centroid')} "
            f"{mode} onset {g('onset_rate', '{:.1f}')}")


def sheet(k: int, size: int) -> None:
    db, home = open_db()
    ids = [int(x) for x in SEED_FILE.read_text().split()][k * size:(k + 1) * size]
    got, names, P = clap._scores(db, home, ids)
    zs = {tid: p for tid, p in zip(got, P)}
    for tid in ids:
        t = db.get_track(tid)
        web = webtags.get_web(db, tid)
        ab = ""
        if web and web["ab"]:
            a = web["ab"]
            ab = " AB[" + " ".join(f"{k[5:8]}{a[k]:.2f}" for k in ("mood_sad", "mood_happy", "mood_relaxed", "mood_aggressive",
                                                                    "mood_party") if k in a) + "]"
        tags = ""
        if web and web["tags"]:
            tags = " tags:" + ",".join(x[0] for x in web["tags"][:4])
        top = ""
        if tid in zs:
            order = np.argsort(-zs[tid])[:3]
            top = " CLAP:" + "/".join(f"{names[j][:5]}{zs[tid][j]:.2f}" for j in order)
        feat = f" feat.{','.join(t['feat'])}" if t["feat"] else ""
        print(f"{tid}|{t['artist']}{feat} — {t['title']} ({t['album'] or '-'},{t['year'] or '-'})|"
              f"{fmt_desc(db.get_descriptors(tid))}{ab}{top}{tags}")


def apply(path: str) -> None:
    db, _ = open_db()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    n = 0
    for tid, moods in data.items():
        moods = [m for m in moods if m in VOCAB]
        if not moods:
            continue
        db.set_moods([int(tid)], moods, "set")
        db.conn.execute("UPDATE track_moods SET source='seed' WHERE track_id=? AND source='manual'", (int(tid),))
        n += 1
    print(f"{n} tracks labelled (source 'seed')")


def stats() -> None:
    db, _ = open_db()
    for r in db.conn.execute("SELECT m.name, tm.source, COUNT(*) FROM track_moods tm JOIN moods m ON m.id=tm.mood_id "
                             "GROUP BY m.name, tm.source ORDER BY m.name"):
        print(tuple(r))
    print("seed tracks labelled:", db.conn.execute("SELECT COUNT(DISTINCT track_id) FROM track_moods WHERE source='seed'").fetchone()[0])


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "select":
        select(int(sys.argv[2]) if len(sys.argv) > 2 else 600)
    elif cmd == "sheet":
        sheet(int(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 60)
    elif cmd == "apply":
        apply(sys.argv[2])
    elif cmd == "stats":
        stats()


def relabel_sheet(k: int, size: int) -> None:
    """Seed-labelled tracks in artist order with their CURRENT moods, to refine them with the expanded vocabulary."""
    db, _ = open_db()
    ids = [r[0] for r in db.conn.execute(
        "SELECT DISTINCT t.id FROM tracks t JOIN track_moods m ON m.track_id=t.id WHERE m.source='seed' AND t.missing=0 "
        "ORDER BY COALESCE(NULLIF(t.albumartist,''),t.artist) COLLATE UNI, t.album COLLATE UNI, t.title")]
    for tid in ids[k * size:(k + 1) * size]:
        t = db.get_track(tid)
        web = webtags.get_web(db, tid)
        tags = (" tags:" + ",".join(x[0] for x in web["tags"][:3])) if web and web["tags"] else ""
        print(f"{tid}|{t['artist']} — {t['title']} ({t['album'] or '-'},{t['year'] or '-'})|{fmt_desc(db.get_descriptors(tid))}|"
              f"NOW:{','.join(m[:5] for m in t['moods'])}{tags}")
    print(f"-- batch {k}: {len(ids[k * size:(k + 1) * size])} of {len(ids)} seed tracks")


if __name__ == "__main__" and sys.argv[1] == "relabel":
    relabel_sheet(int(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 70)
