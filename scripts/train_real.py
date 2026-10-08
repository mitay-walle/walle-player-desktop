"""Train the mood model on the real catalogue: compares feature sets with the honest artist-grouped check, saves the best."""
import sys, time, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from musicplayer.core import mood
from musicplayer.core.db import Database
from musicplayer.core.paths import data_dir
from musicplayer.core import extprogress

home = data_dir()
db = Database(home / "library.db")
web = {"auto": "auto", "never": False, "always": True}[sys.argv[2]] if len(sys.argv) > 2 else False
modes = [sys.argv[1]] if len(sys.argv) > 1 and sys.argv[1] != "compare" else ["descriptors", "clap", "both"]
res = {}
for m in modes:
    t0 = time.time()
    with extprogress.job_context(home, "Обучение модели настроений",
                                 log=lambda s: print("   ", s) if s.startswith("  ") or "Честная" in s or "Соседи" in s else None) as jctx:
        r = mood.train(db, home / ("models" if len(modes) == 1 else f"models_eval_{m}"), epochs=150, features=m, web=web,
                       ctx=jctx)
    res[m] = r
    print(f"{m:12s} chosen={r['chosen']:34s} honest AUC={r['val']['auc']:.3f} F1={r['val']['f1']:.3f} top1={r['val']['top1']:.3f} "
          f"| known-artists AUC={r['auc_random_split']:.3f} | ECE {r['ece_raw']:.3f}->{r['ece_calibrated']:.3f} | "
          f"tracks={r['tracks']} artists={r['n_groups']} | {time.time()-t0:.0f}s", flush=True)
    print("   per mood AUC:", {k: round(v['val_auc'], 2) for k, v in r['per_mood'].items()}, flush=True)
