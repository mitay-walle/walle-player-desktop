"""CLAP embeddings for every track that has none yet (new scans), then retrain and re-tag. Progress shows in the app."""
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from musicplayer.core import clap, extprogress  # noqa: E402
from musicplayer.core.db import Database  # noqa: E402
from musicplayer.core.paths import data_dir  # noqa: E402

home = data_dir()
db = Database(home / "library.db")
ids = [r[0] for r in db.conn.execute("SELECT id FROM tracks WHERE missing=0") if not db.get_embedding(r[0], "clap")]
labelled = {t for t, _ in db.mood_training_set()}
ids.sort(key=lambda t: t not in labelled)            # labelled tracks first: they matter for training
print("without CLAP:", len(ids), "of them labelled:", sum(t in labelled for t in ids), flush=True)
if not clap.available():
    sys.exit("CLAP unavailable: " + clap.unavailable_reason())
with extprogress.job_context(home, "Отпечатки звука CLAP для новых треков") as ctx:
    print(clap.embed_tracks(db, ids, ctx), flush=True)
