"""Self-improvement loop on the REAL catalogue (background; progress shows in the app):

    round:  1. fetch internet data (MusicBrainz + AcousticBrainz moods) for the next batch of tracks
            2. train model variants (feature sets x network size)
            3. score each: honest artist-grouped CV on YOUR labels + agreement with AcousticBrainz on tracks you did
               not label
            4. install the best variant, re-tag the library with it, log the round
    until every track has internet data (or --rounds N).

    python scripts/web_loop.py [--batch 300] [--rounds 99]
Log: <data>/web_loop_log.json (one entry per round).  Your own labels are never changed.
"""
import argparse
import json
import shutil
import sys
import time
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from musicplayer.core import extprogress, mood, webeval, webtags  # noqa: E402
from musicplayer.core.db import Database  # noqa: E402
from musicplayer.core.paths import data_dir  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--batch", type=int, default=300)
ap.add_argument("--rounds", type=int, default=99)
args = ap.parse_args()

home = data_dir()
db = Database(home / "library.db")
log_path = home / "web_loop_log.json"
log = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
VARIANTS = [{"features": f, "hidden": 48} for f in ("both", "clap", "descriptors")]


def todo_ids() -> list[int]:
    have = {r[0] for r in db.conn.execute("SELECT track_id FROM track_web")}
    # unlabelled tracks first: they are where the internet check is honest
    labelled = {t for t, _ in db.mood_training_set()}
    ids = [r[0] for r in db.conn.execute("SELECT id FROM tracks WHERE missing=0 ORDER BY id") if r[0] not in have]
    return sorted(ids, key=lambda t: t in labelled)


for rnd in range(1, args.rounds + 1):
    left = todo_ids()
    entry = {"round": len(log) + 1, "at": time.strftime("%Y-%m-%d %H:%M")}
    with extprogress.job_context(home, f"Цикл обучения, раунд {entry['round']}: интернет-данные", log=None) as ctx:
        if left:
            r = webtags.fetch_tracks(db, left[:args.batch], ctx=ctx)
            db.conn.commit()
            entry["fetched"] = r
    truth = webeval.web_truth(db)
    entry["web_truth"] = {m: {"tracks": len(d), "positive": sum(d.values())} for m, d in truth.items()}
    results = []
    with extprogress.job_context(home, f"Цикл обучения, раунд {entry['round']}: варианты модели", log=None) as ctx:
        for i, v in enumerate(VARIANTS):
            ctx.progress(i, len(VARIANTS), f"{v['features']}, сеть {v['hidden']}")
            out = home / f"models_loop_{v['features']}_{v['hidden']}"
            try:
                res = mood.train(db, out, epochs=150, features=v["features"], hidden=v["hidden"], web=False)
            except ValueError as e:
                results.append({**v, "error": str(e)})
                continue
            agr = webeval.agreement(db, mood.load_model(out), truth)
            honest = res["val"]["auc"]
            web_auc = agr["mean_auc"]
            score = honest if web_auc is None else 0.6 * honest + 0.4 * web_auc
            results.append({**v, "dir": str(out), "chosen": res["chosen"], "honest_auc": round(honest, 3),
                            "top1": round(res["val"]["top1"], 3), "web_auc": web_auc, "web_per_mood": agr["per_mood"],
                            "web_tracks": agr["tracks"], "score": round(score, 4)})
            print(f"  {v}: honest {honest:.3f}, web {web_auc}, score {score:.3f}", flush=True)
    ok = [r for r in results if "score" in r]
    entry["variants"] = results
    if ok:
        best = max(ok, key=lambda r: r["score"])
        entry["best"] = {k: best[k] for k in ("features", "hidden", "chosen", "honest_auc", "top1", "web_auc", "score")}
        shutil.copy2(best["dir"] + "/mood_model.pkl", home / "models" / "mood_model.pkl")
        with extprogress.job_context(home, f"Цикл обучения, раунд {entry['round']}: разметка библиотеки", log=None) as ctx:
            entry["applied"] = mood.predict(db, home / "models", db.unlabelled_ids(), auto_mode="balanced", ctx=ctx)
        db.conn.commit()
        print(f"round {entry['round']}: best {entry['best']}", flush=True)
    log.append(entry)
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    if not todo_ids():
        print("all tracks have internet data: done", flush=True)
        break
