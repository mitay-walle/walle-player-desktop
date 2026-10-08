"""Export / import the hand-made labels so they live in git (small JSON keyed by FILE PATH, not by database id).

    python scripts/labels_io.py export [out.json]   # real catalogue -> labels/labels.json
    python scripts/labels_io.py import [in.json]    # labels.json -> catalogue (matches tracks by path)

Exported: moods with source manual/seed (what the model trains on), vocal/instrumental and language set by manual/seed.
Automatic labels are NOT exported: they are reproducible from the model.
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

DEFAULT = ROOT / "labels" / "labels.json"


def export(out: Path) -> None:
    db = Database(data_dir() / "library.db")
    moods: dict[str, dict] = {}
    for r in db.conn.execute(
            "SELECT t.path, m.name, tm.source FROM track_moods tm JOIN tracks t ON t.id=tm.track_id JOIN moods m ON m.id=tm.mood_id "
            "WHERE tm.source IN ('manual','seed') ORDER BY t.path, m.name"):
        d = moods.setdefault(r["path"], {"moods": [], "src": r["source"]})
        d["moods"].append(r["name"])
        if r["source"] == "manual":
            d["src"] = "manual"
    vocal = {r["path"]: r["vocal"] for r in db.conn.execute(
        "SELECT path, vocal FROM tracks WHERE vocal IS NOT NULL AND vocal_src IN ('manual','seed') ORDER BY path")}
    lang = {r["path"]: r["lang"] for r in db.conn.execute(
        "SELECT path, lang FROM tracks WHERE lang IS NOT NULL AND lang_src IN ('manual','seed') ORDER BY path")}
    rejects = [[r["path"], r["name"]] for r in db.conn.execute(
        "SELECT t.path, m.name FROM mood_rejects x JOIN tracks t ON t.id=x.track_id JOIN moods m ON m.id=x.mood_id ORDER BY t.path")]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"version": 1, "moods": moods, "vocal": vocal, "lang": lang, "mood_rejects": rejects},
                              ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"exported {len(moods)} labelled tracks, {len(vocal)} vocal flags, {len(lang)} languages, {len(rejects)} rejects -> {out}")


def import_(src: Path) -> None:
    db = Database(data_dir() / "library.db")
    data = json.loads(src.read_text(encoding="utf-8"))
    ids = {r["path"]: r["id"] for r in db.conn.execute("SELECT id, path FROM tracks")}
    n = miss = 0
    for path, d in data["moods"].items():
        tid = ids.get(path)
        if tid is None:
            miss += 1
            continue
        db.set_moods([tid], d["moods"], "set")
        db.conn.execute("UPDATE track_moods SET source=? WHERE track_id=? AND source='manual'", (d["src"], tid))
        n += 1
    for path, v in data.get("vocal", {}).items():
        if path in ids:
            db.set_vocal([ids[path]], v, src="seed")
    for path, v in data.get("lang", {}).items():
        if path in ids:
            db.set_language([ids[path]], v, src="manual")
    print(f"imported {n} labelled tracks ({miss} paths not in this catalogue)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "export"
    path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT
    export(path) if cmd == "export" else import_(path)
