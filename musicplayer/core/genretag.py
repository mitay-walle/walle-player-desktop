"""Approximate genres from the internet: MusicBrainz tags of each ARTIST mapped onto the genre tree (core/genres.py).

Only the catalogue is changed (track_genres, mode "add"); the music files are not touched.  Artist tags are cached in
``web_cache`` (see webtags.artist_tags), so a re-run is quick."""

from __future__ import annotations

from .db import Database
from .genres import GENRE_TREE, PARENT, canonical
from .jobs import JobContext
from .webtags import Http, _ensure, artist_tags

# tag substring -> sub-genre (checked first, most specific first)
SUB_RULES = [
    ("post-hardcore", "Post-hardcore"), ("post hardcore", "Post-hardcore"), ("metalcore", "Metalcore"),
    ("djent", "Djent"), ("power metal", "Power metal"), ("nu metal", "Nu metal"), ("nu-metal", "Nu metal"),
    ("emocore", "Emocore"), ("screamo", "Emocore"), ("emo", "Emocore"), ("pop punk", "Pop punk"), ("pop-punk", "Pop punk"),
    ("hardcore punk", "Hardcore"), ("grunge", "Grunge"), ("post-punk", "Post-punk"), ("post punk", "Post-punk"),
    ("alternative", "Alternative"), ("drum and bass", "Drum and bass"), ("drum & bass", "Drum and bass"),
    ("drum n bass", "Drum and bass"), ("dnb", "Drum and bass"), ("jungle", "Drum and bass"), ("synthwave", "Synthwave"),
    ("retrowave", "Synthwave"), ("outrun", "Synthwave"), ("darksynth", "Synthwave"), ("ambient", "Ambient"),
    ("techno", "Techno"), ("grime", "Grime"), ("russian hip hop", "Russian Rap"), ("russian rap", "Russian Rap"),
    ("video game", "Games"), ("game music", "Games"), ("vgm", "Games"), ("chiptune", "8-bit"), ("8-bit", "8-bit"), ("8bit", "8-bit"),
    ("bitpop", "8-bit"), ("nintendocore", "8-bit"),
]
# tag substring -> family
FAMILY_RULES = [
    ("metal", "Metal"), ("deathcore", "Metal"), ("punk", "Punk"), ("hardcore", "Punk"), ("rock", "Rock"),
    ("hip hop", "Hip-hop"), ("hip-hop", "Hip-hop"), ("rap", "Hip-hop"), ("trap", "Hip-hop"),
    ("electronic", "Electronic"), ("electro", "Electronic"), ("edm", "Electronic"), ("house", "Electronic"),
    ("trance", "Electronic"), ("dubstep", "Electronic"), ("breakbeat", "Electronic"), ("idm", "Electronic"),
    ("big beat", "Electronic"), ("industrial", "Electronic"), ("soundtrack", "Soundtrack"), ("score", "Soundtrack"),
    ("anime", "Soundtrack"), ("jazz", "Jazz"), ("swing", "Jazz"), ("bebop", "Jazz"), ("big band", "Jazz"),
    ("bossa nova", "Jazz"), ("vocal jazz", "Jazz"), ("oriental", "Oriental"), ("arabic", "Oriental"), ("middle eastern", "Oriental"),
    ("turkish", "Oriental"), ("persian", "Oriental"), ("ethnic", "Oriental"), ("world", "Oriental"), ("pop", "Pop"),
]
IGNORE = ("hardcore techno", "happy hardcore", "hardcore dance")


def map_tags(tags: list[list], lang: str | None = None) -> list[str]:
    """[[tag, count]] -> genres of the tree: up to 2 families and 3 sub-genres among the artist's main tags."""
    if not tags:
        return []
    top = max(c for _, c in tags) or 1
    main = [t for t, c in tags if c >= max(1, 0.3 * top)] or [tags[0][0]]
    subs: list[str] = []
    fams: list[str] = []
    for tag in main:
        tag = tag.lower()
        if any(x in tag for x in IGNORE):
            fams.append("Electronic")
            continue
        sub = next((g for k, g in SUB_RULES if k in tag), None)
        if sub == "Emocore" and tag not in ("emo", "emocore", "screamo") and not tag.startswith("emo "):
            sub = None                                   # "emotional", "demo" ... are not emo
        if sub == "Techno" and "techno" not in tag.split():
            sub = None if "technical" in tag else sub
        sub = canonical(sub)[0] if sub else None          # the rules speak English; the catalogue - Russian
        if sub and sub not in subs:
            subs.append(sub)
        fam = PARENT.get(sub) if sub else next((g for k, g in FAMILY_RULES if k in tag), None)
        fam = canonical(fam)[0] if fam else None
        if fam and fam not in fams:
            fams.append(fam)
    if "Hip-Hop" in fams and lang == "ru" and "Russian Rap" not in subs:
        subs.append("Russian Rap")
    fams = fams[:2]
    subs = [s for s in subs if PARENT[s] in fams][:3]
    return fams + subs


def tag_library(db: Database, ctx: JobContext | None = None, http: Http | None = None, only_empty: bool = False) -> dict:
    """Fetch artist tags and add the mapped genres to every track of the artist.  Returns counts."""
    ctx = ctx or JobContext()
    http = http or Http()
    _ensure(db)
    rows = db.conn.execute("SELECT id, COALESCE(NULLIF(albumartist,''), artist) AS a, lang FROM tracks WHERE missing=0").fetchall()
    by_artist: dict[str, list] = {}
    for r in rows:
        if r["a"]:
            by_artist.setdefault(r["a"], []).append(r)
    known = {g.lower() for fam, subs in GENRE_TREE.items() for g in [fam, *subs]}
    done = tagged = tracks = 0
    total = len(by_artist)
    for artist, trs in sorted(by_artist.items()):
        ctx.check()
        langs = [t["lang"] for t in trs if t["lang"]]
        lang = max(set(langs), key=langs.count) if langs else None
        genres = map_tags(artist_tags(db, http, artist), lang)
        db.conn.commit()                                 # the cache entry
        if genres:
            ids = [t["id"] for t in trs]
            if only_empty:
                have = {r[0] for r in db.conn.execute(
                    f"SELECT DISTINCT track_id FROM track_genres WHERE track_id IN ({','.join('?' * len(ids))})", ids)}
                ids = [i for i in ids if i not in have]
            if ids:
                db.set_genres(ids, genres, "add")
                db.conn.commit()
                tagged += 1
                tracks += len(ids)
        done += 1
        ctx.progress(done, total, f"{artist}: {', '.join(genres) or '—'}")
    del known
    return {"artists": total, "artists_tagged": tagged, "tracks": tracks}
