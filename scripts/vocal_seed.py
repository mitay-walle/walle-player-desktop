"""Training examples for instrumental / vocals on the REAL catalogue (Claude's knowledge of the music).

Instrumental: every track of the artists below (ambient, post-rock, game / film scores, instrumental electronic), plus
intros / outros / interludes.  Vocals: the other tracks I labelled earlier (seed moods).  The classifier then learns the
sound of vocals from CLAP + descriptors and decides the rest of the library."""
import re, sys, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
from musicplayer.core import langvocal
from musicplayer.core.db import Database
from musicplayer.core.paths import data_dir

INSTRUMENTAL_ARTISTS = {
    "amethystium", "arvi teikari", "balmorhea", "brian eno", "carbon based lifeforms", "dance with the dead", "ddrkirby(isq)",
    "earthworm jim 2 (t.tallarico)", "everybody's gone to the rapture", "everybody's gone to the rapture ost", "geek music",
    "heavy metal f.a.k.k.2", "ilya beshevli", "jeremy soule", "jessica curry", "joe hisaishi", "kyle dixon", "max payne", "ost max payne",
    "mike dawes", "n", "nemertines", "paul romero", "pawel blaszczak", "plini", "rameses b", "sephiroth", "sonya belousova",
    "subnautica", "subnautica ost", "the mark kramer trio", "the neon droid", "the pianoguys", "ólafur arnalds", "yoko shimomura",
    "intervals", "snarky puppy", "dj ten", "高木正勝", "darren korb", "arvi teikari"}
TITLE_INSTRUMENTAL = re.compile(r"\b(intro|outro|interlude|instrumental|theme|ost)\b|интро|аутро", re.I)

db = Database(data_dir() / "library.db")
inst = voc = 0
for r in db.conn.execute("SELECT id, title, COALESCE(NULLIF(albumartist,''),artist) a FROM tracks WHERE missing=0").fetchall():
    if r["a"].casefold() in INSTRUMENTAL_ARTISTS or TITLE_INSTRUMENTAL.search(r["title"] or ""):
        db.set_vocal([r["id"]], "inst", src="seed"); inst += 1
seed_ids = [x[0] for x in db.conn.execute("SELECT DISTINCT track_id FROM track_moods WHERE source IN ('seed','manual')")]
for tid in seed_ids:
    if not db.conn.execute("SELECT vocal FROM tracks WHERE id=?", (tid,)).fetchone()[0]:
        db.set_vocal([tid], "voc", src="seed"); voc += 1
print(f"instrumental examples {inst}, vocal examples {voc}")
home = data_dir()
info = langvocal.train_vocals(db, home / "models")
print("vocals classifier (honest, grouped by artist):", {k: round(v, 3) if isinstance(v, float) else v for k, v in info.items()})
print(langvocal.predict_vocals(db, home / "models"))
print("languages:", langvocal.apply_languages(db))
c = db.conn
print("vocal counts:", [tuple(r) for r in c.execute("SELECT vocal, COUNT(*) FROM tracks WHERE missing=0 GROUP BY vocal")])
print("lang counts:", [tuple(r) for r in c.execute("SELECT lang, COUNT(*) FROM tracks WHERE missing=0 GROUP BY lang")])
print("auto-predicted instrumental artists (top):")
for r in c.execute("SELECT COALESCE(NULLIF(albumartist,''),artist) a, COUNT(*) n FROM tracks WHERE vocal='inst' AND vocal_src='auto' AND missing=0 GROUP BY a ORDER BY n DESC LIMIT 25"):
    print("  ", r["a"], r["n"])
