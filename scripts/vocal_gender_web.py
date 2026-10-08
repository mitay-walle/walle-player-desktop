"""Male / female vocals for the REAL catalogue from MusicBrainz (background; progress shows in the app)."""
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from musicplayer.core import extprogress, vocalgender  # noqa: E402
from musicplayer.core.db import Database  # noqa: E402
from musicplayer.core.paths import data_dir  # noqa: E402

home = data_dir()
db = Database(home / "library.db")
with extprogress.job_context(home, "Мужской / женский вокал (MusicBrainz)") as ctx:
    print(vocalgender.tag_library(db, ctx), flush=True)
