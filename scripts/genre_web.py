"""Approximate genres for the REAL catalogue from MusicBrainz artist tags (background; progress shows in the app).

    python scripts/genre_web.py            # add mapped genres to every track
"""
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from musicplayer.core import extprogress, genretag  # noqa: E402
from musicplayer.core.db import Database  # noqa: E402
from musicplayer.core.paths import data_dir  # noqa: E402

home = data_dir()
db = Database(home / "library.db")
with extprogress.job_context(home, "Жанры из интернета (MusicBrainz)") as ctx:
    print(genretag.tag_library(db, ctx), flush=True)
for r in db.conn.execute("SELECT genre, COUNT(*) FROM track_genres GROUP BY genre ORDER BY 2 DESC LIMIT 30"):
    print("  ", tuple(r))
