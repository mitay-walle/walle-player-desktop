"""Re-read AcousticBrainz moods for every recording already found (after the 'Not sad' class-name fix)."""
import json
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from musicplayer.core import extprogress, webtags  # noqa: E402
from musicplayer.core.db import Database  # noqa: E402
from musicplayer.core.paths import data_dir  # noqa: E402

home = data_dir()
db = Database(home / "library.db")
webtags._ensure(db)
db.conn.execute("DELETE FROM web_cache WHERE key LIKE 'ab:%'")
db.conn.commit()
rows = db.conn.execute("SELECT track_id, mbid FROM track_web WHERE mbid IS NOT NULL").fetchall()
mbids = sorted({r[1] for r in rows})
http = webtags.Http()
got: dict = {}
with extprogress.job_context(home, "AcousticBrainz: перечитываю настроения", log=None) as ctx:
    for i in range(0, len(mbids), 25):
        got.update(webtags.acousticbrainz(db, http, mbids[i:i + 25]))
        db.conn.commit()
        ctx.progress(min(i + 25, len(mbids)), len(mbids), f"найдено {len(got)}")
n = 0
for tid, mbid in rows:
    if mbid in got:
        db.conn.execute("UPDATE track_web SET ab=? WHERE track_id=?", (json.dumps(got[mbid]), tid))
        n += 1
db.conn.commit()
print({"recordings": len(mbids), "with_moods": len(got), "tracks_updated": n})
