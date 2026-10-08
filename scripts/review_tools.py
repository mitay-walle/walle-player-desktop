"""Track-by-track review of the model's moods (Claude as the reviewer), in library order.

    python scripts/review_tools.py list [size]    # next `size` unreviewed tracks: signals + what the model decided
    python scripts/review_tools.py progress       # how many tracks were reviewed

A review batch is applied through the RUNNING app (MCP tool review_apply) so the window updates live.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from musicplayer.core.db import Database  # noqa: E402
from musicplayer.core.paths import data_dir  # noqa: E402

BATCH_FILE = ROOT / "data" / "bootstrap" / "review_batch.json"
SHORT = {"Весёлое": "Вес", "Грустное": "Гру", "Энергичное": "Эне", "Спокойное": "Спо", "Злое": "Агр",
         "Меланхоличное": "Ром", "Ностальгия": "Нос", "Фоновое": "Фон"}


def desc_line(d: dict | None) -> str:
    if not d:
        return "—"
    def g(k):
        v = d.get(k)
        return f"{v:.0f}" if isinstance(v, (int, float)) else "?"
    ton = str(d.get("tonality") or "").lower()
    return f"bpm{g('bpm')} L{g('loudness')} {'min' if 'minor' in ton else 'maj' if 'major' in ton else '?'}"


def main() -> None:
    db = Database(data_dir() / "library.db")
    db.conn.execute("CREATE TABLE IF NOT EXISTS reviewed(track_id INTEGER PRIMARY KEY, at REAL)")
    cmd = sys.argv[1]
    if cmd == "progress":
        n = db.conn.execute("SELECT COUNT(*) FROM reviewed").fetchone()[0]
        total = db.conn.execute("SELECT COUNT(*) FROM tracks WHERE missing=0").fetchone()[0]
        seed = db.conn.execute("SELECT COUNT(DISTINCT track_id) FROM track_moods WHERE source IN ('seed','manual')").fetchone()[0]
        print(f"reviewed {n} of {total}; trained-on labels (seed+manual): {seed}")
        return
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 40
    rows = db.conn.execute(
        "SELECT t.id FROM tracks t WHERE t.missing=0 AND t.id NOT IN (SELECT track_id FROM reviewed) "
        "AND NOT EXISTS(SELECT 1 FROM track_moods m WHERE m.track_id=t.id AND m.source IN ('seed','manual')) "
        "ORDER BY COALESCE(NULLIF(t.albumartist,''),t.artist) COLLATE UNI, t.album COLLATE UNI, t.disc_no, t.track_no, t.title "
        "LIMIT ?", (size,)).fetchall()
    ids = [r[0] for r in rows]
    BATCH_FILE.write_text(json.dumps(ids), encoding="utf-8")
    for tid in ids:
        t = db.get_track(tid)
        auto = [f"{SHORT[m]}{t['mood_conf'].get(m) or 0:.2f}" for m in t["moods"]]
        sug = [f"{SHORT.get(s['mood'], s['mood'][:3])}?{s['confidence'] or 0:.2f}" for s in t["mood_suggestions"]]
        feat = f" feat.{','.join(t['feat'])}" if t["feat"] else ""
        print(f"{tid}|{t['artist']}{feat} — {t['title']} ({t['album'] or '-'},{t['year'] or '-'})|{desc_line(db.get_descriptors(tid))}|"
              f"{' '.join(auto) or '-'}  {' '.join(sug)}")


if __name__ == "__main__":
    main()
