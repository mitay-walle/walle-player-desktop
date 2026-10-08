"""Apply the trained model to every unlabelled track of the real catalogue (features are already computed)."""
import sys, time, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from musicplayer.core import autotag, mood
from musicplayer.core.db import Database
from musicplayer.core import extprogress
from musicplayer.core.paths import data_dir

mode = sys.argv[1] if len(sys.argv) > 1 else "balanced"
home = data_dir()
db = Database(home / "library.db")
print("model:", mood.load_model(home / "models").info["chosen"], "| pending tracks:", len(autotag.pending_ids(db)))
ids = [t for t in db.unlabelled_ids()]          # also re-predicts earlier auto labels
with extprogress.job_context(home, "Разметка библиотеки моделью") as jctx:
    r = mood.predict(db, home / "models", ids, auto_mode=mode, ctx=jctx)
print(r)
c = db.conn
print("tracks with auto mood:", c.execute("SELECT COUNT(DISTINCT track_id) FROM track_moods WHERE source='auto'").fetchone()[0],
      "| tracks with only suggestions:", c.execute("SELECT COUNT(DISTINCT track_id) FROM track_moods WHERE source='model' AND track_id NOT IN (SELECT track_id FROM track_moods WHERE source IN ('auto','seed','manual'))").fetchone()[0])
for row in c.execute("SELECT m.name, SUM(tm.source='auto'), SUM(tm.source='seed') FROM track_moods tm JOIN moods m ON m.id=tm.mood_id GROUP BY m.name ORDER BY 2 DESC"):
    print("  ", tuple(row))
