"""Heavy one-off pipeline on the REAL catalogue (default home: %LOCALAPPDATA%\\WallePlayer):

    python scripts/bootstrap_library.py scan           # scan the music folder (read-only)
    python scripts/bootstrap_library.py descriptors    # audio descriptors for every track (CPU processes)
    python scripts/bootstrap_library.py clap           # CLAP embeddings for every track (GPU)
    python scripts/bootstrap_library.py web [seed]     # internet tags / AcousticBrainz moods (network, ~3 s/track)

Each step is resumable (it only does what is missing) and logs progress to data/bootstrap/<step>.log.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from musicplayer.core import clap, mood, scanner, webtags  # noqa: E402
from musicplayer.core.db import Database  # noqa: E402
from musicplayer.core.jobs import JobContext  # noqa: E402
from musicplayer.ui.context import AppContext, DEFAULT_ROOT  # noqa: E402


def context() -> tuple[Database, Path, Path]:
    from musicplayer.core.paths import data_dir
    home = data_dir()
    return Database(home / "library.db"), home / "covers", home


def make_ctx(step: str) -> JobContext:
    log_dir = ROOT / "data" / "bootstrap"
    log_dir.mkdir(parents=True, exist_ok=True)
    f = open(log_dir / f"{step}.log", "a", encoding="utf-8", buffering=1)
    t0, last = time.time(), [0.0]

    def prog(d: int, t: int, m: str) -> None:
        if time.time() - last[0] > 15 or (t and d >= t):
            last[0] = time.time()
            line = f"[{int(time.time() - t0)}s] {d}/{t} {m}"
            print(line, flush=True)
            f.write(line + "\n")

    def log(m: str) -> None:
        print(m, flush=True)
        f.write(m + "\n")
    return JobContext(prog, log, min_interval=0.0)


def main() -> None:
    step = sys.argv[1]
    db, covers, home = context()
    ctx = make_ctx(step)
    print(f"catalogue: {home / 'library.db'}  tracks: {db.conn.execute('SELECT COUNT(*) FROM tracks').fetchone()[0]}")
    if step == "scan":
        root = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_ROOT
        r = scanner.scan(db, root, covers, lambda d, t, m: ctx.progress(d, t, m))
        print({k: v for k, v in r.items() if k != "errors"}, "errors:", len(r["errors"]))
    elif step == "descriptors":
        workers = int(os.environ.get("WORKERS", "6"))
        print(mood.analyze_tracks(db, None, ctx, workers=workers))
    elif step == "clap":
        print(clap.embed_tracks(db, None, ctx))
    elif step == "web":
        ids = None
        if len(sys.argv) > 2 and sys.argv[2] == "seed":
            ids = [int(x) for x in open(ROOT / "data" / "bootstrap" / "seed_ids.txt").read().split()]
        print(webtags.fetch_tracks(db, ids, ctx))
    else:
        raise SystemExit(f"unknown step {step}")


if __name__ == "__main__":
    main()
