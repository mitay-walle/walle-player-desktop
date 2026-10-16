"""The genre vocabulary: one canonical (English) name per genre, a family -> sub-genre tree, and aliases.

Genres are free text in the catalogue (track_genres), but every write goes through :func:`normalize`, so all clients
(the desktop app, the web player, the phone apps) see the same thing:

* one name per genre - «Hip-Hop», not Hip-hop / Hip Hop / Rap / Хип-хоп / рэп side by side (aliases in any language
  and spelling map to the canonical name; junk like «Other» or a download site's name is dropped);
* tags glued together by a separator - «Post-Hardcore | Pop-Punk», «Ambient, Psybient, Downtempo» - are split;
* inheritance is stored, not guessed: a sub-genre always brings its family (Pop Punk -> also Punk), so searching or
  filtering by a family finds its sub-genres in every client without knowing the tree.

Genres are English only: what the user types with Cyrillic letters is refused (:func:`check_latin`), a Russian tag
from a file is translated when the vocabulary knows it and dropped otherwise.  Russian names stay aliases for file
tags and search (``RUSSIAN``), so a search for «рок» still finds Rock.
Genres outside the tree are kept (trimmed, first letter capitalised) - the user may add his own, in English.
"""

from __future__ import annotations

import re

GENRE_TREE: dict[str, list[str]] = {
    "Rock": ["Alternative", "Indie", "Grunge", "Post-Punk", "Hard Rock", "Pop Rock", "Progressive Rock", "J-Rock"],
    "Punk": ["Pop Punk", "Ska Punk", "Hardcore", "Emocore"],
    "Metal": ["Metalcore", "Post-Hardcore", "Deathcore", "Djent", "Power Metal", "Nu Metal", "Industrial Metal"],
    "Electronic": ["Techno", "Drum and Bass", "Breaks", "Synthwave", "Chillwave", "8-bit", "Ambient", "Downtempo",
                   "Psybient", "Berlin School"],
    "Hip-Hop": ["Russian Rap", "Grime", "Hypecore"],
    "Pop": ["Dark Pop"],
    "Jazz": ["Blues"],
    "Folk": [],
    "Soundtrack": ["Games"],
    "Oriental": [],
}
PARENT = {sub: fam for fam, subs in GENRE_TREE.items() for sub in subs}
GENRE_CHIPS = [g for fam, subs in GENRE_TREE.items() for g in [fam, *subs]]
FAMILY_COLOURS = {"Rock": "#E8A87C", "Punk": "#F28B82", "Metal": "#AEB8C6", "Electronic": "#7FD6E0",
                  "Hip-Hop": "#C5A8F5", "Pop": "#F5A9C4", "Jazz": "#C9A27A", "Folk": "#A8D08D", "Soundtrack": "#D9BE95",
                  "Oriental": "#E0C27A"}
ERAS = [1960, 1970, 1980, 1990, 2000, 2010, 2020]

# the Russian name of each canonical genre: an alias, and indexed for search («рок» finds Rock)
RUSSIAN = {"Rock": "Рок", "Alternative": "Альтернатива", "Indie": "Инди", "Grunge": "Гранж", "Post-Punk": "Пост-панк",
           "Hard Rock": "Хард-рок", "Pop Rock": "Поп-рок", "Progressive Rock": "Прогрессив-рок", "J-Rock": "Джей-рок",
           "Punk": "Панк", "Pop Punk": "Поп-панк", "Ska Punk": "Ска-панк", "Hardcore": "Хардкор", "Emocore": "Эмокор",
           "Metal": "Метал", "Metalcore": "Металкор", "Post-Hardcore": "Пост-хардкор", "Deathcore": "Дэткор",
           "Djent": "Джент", "Power Metal": "Пауэр-метал", "Nu Metal": "Ню-метал", "Industrial Metal": "Индастриал-метал",
           "Electronic": "Электроника", "Techno": "Техно", "Drum and Bass": "Драм-н-бейс", "Breaks": "Брейкс",
           "Synthwave": "Синтвейв", "Chillwave": "Чилвейв", "8-bit": "8-бит", "Ambient": "Эмбиент",
           "Downtempo": "Даунтемпо", "Psybient": "Псайбиент", "Berlin School": "Берлинская школа", "Hip-Hop": "Хип-хоп", "Russian Rap": "Русский рэп",
           "Grime": "Грайм", "Hypecore": "Хайпкор", "Pop": "Поп", "Dark Pop": "Дарк-поп", "Jazz": "Джаз",
           "Blues": "Блюз", "Folk": "Фолк", "Soundtrack": "Саундтрек", "Games": "Игры", "Oriental": "Восточная"}

# alias -> canonical name(s); keys are compared after :func:`_key` (case, ё, punctuation, «&»/«and» do not matter)
_ALIASES: dict[str, str | tuple[str, ...]] = {
    # families
    "noir rock": "Rock", "rock and roll": "Rock", "rock n roll": "Rock", "jrock": "J-Rock", "japanese rock": "J-Rock",
    "punk rock": "Punk", "street punk": "Punk", "skate punk": "Punk", "скейт панк": "Punk", "art punk": "Punk",
    "garage punk": "Punk", "post punk revival": "Post-Punk",
    "heavy metal": "Metal", "melodic death metal": "Metal", "death metal": "Metal", "thrash metal": "Metal",
    "black metal": "Metal", "grind": "Metal", "grindcore": "Metal", "sludge": "Metal", "progressive metal": "Metal",
    "ndh": "Industrial Metal", "neue deutsche harte": "Industrial Metal", "neue deutsche härte": "Industrial Metal",
    "alt metal": "Metal", "alternative metal": "Metal", "extreme metal": "Metal", "экстремальный метал": "Metal",
    "дезкор": "Deathcore",
    "electronica": "Electronic", "electro": "Electronic", "electronics": "Electronic", "electronique": "Electronic",
    "électronique": "Electronic", "электронная музыка": "Electronic", "experimental": "Electronic",
    "dance": "Electronic", "edm": "Electronic", "house": "Electronic", "electro house": "Electronic",
    "trance": "Electronic", "dubstep": "Electronic", "idm": "Electronic", "industrial": "Electronic",
    "hip hop": "Hip-Hop", "hiphop": "Hip-Hop", "rap": "Hip-Hop", "рэп": "Hip-Hop", "реп": "Hip-Hop",
    "хип хоп": "Hip-Hop", "альтернативный рэп": "Hip-Hop", "trap": "Hip-Hop",
    "indie pop": "Pop", "поп музыка": "Pop",
    "swing": "Jazz", "bebop": "Jazz", "big band": "Jazz", "bossa nova": "Jazz",
    "acoustic": "Folk", "solo acoustic guitar": "Folk", "acoustic guitar": "Folk",
    "indie folk": "Folk", "singer and songwriter": "Folk", "singer songwriter": "Folk",
    "акустика": "Folk", "бард": "Folk", "авторская песня": "Folk", "русский рок": "Rock", "рок н ролл": "Rock",
    "эстрада": "Pop", "попса": "Pop", "металл": "Metal", "электро": "Electronic", "панк рок": "Punk",
    "ost": "Soundtrack", "score": "Soundtrack", "anime": "Soundtrack", "музыка из фильмов": "Soundtrack",
    "ethnic": "Oriental", "world": "Oriental", "arabic": "Oriental",
    # sub-genres
    "alt rock": "Alternative", "alternative rock": "Alternative", "alternativo": "Alternative",
    "alternative and indie": "Alternative", "альтернативный рок": "Alternative",
    "indie rock": "Indie", "инди рок": "Indie",
    "post grunge": "Grunge", "постгранж": "Grunge",
    "постпанк": "Post-Punk", "хардрок": "Hard Rock",
    "progressive": "Progressive Rock", "prog rock": "Progressive Rock", "psychedelic rock": "Progressive Rock",
    "поппанк": "Pop Punk",
    "ska core": "Ska Punk", "skacore": "Ska Punk", "ska": "Ska Punk", "ска": "Ska Punk", "скапанк": "Ska Punk",
    "third wave ska": "Ska Punk",
    "hardcore punk": "Hardcore",
    "emo": "Emocore", "screamo": "Emocore", "эмо": "Emocore",
    "progressive metalcore": "Metalcore",
    "постхардкор": "Post-Hardcore", "progressive post hardcore": "Post-Hardcore",
    "progressive math metal": "Djent", "math metal": "Djent",
    "numetal": "Nu Metal", "нюметал": "Nu Metal",
    "dnb": "Drum and Bass", "drum n bass": "Drum and Bass", "jungle": "Drum and Bass", "neurofunk": "Drum and Bass",
    "драм н бейс": "Drum and Bass", "драмнбейс": "Drum and Bass",
    "breakbeat": "Breaks", "broken beat": "Breaks",
    "retrowave": "Synthwave", "outrun": "Synthwave", "darksynth": "Synthwave", "spacewave": "Synthwave",
    "dreamwave": "Synthwave",
    "8bit": "8-bit", "chiptune": "8-bit", "bitpop": "8-bit", "nintendocore": "8-bit",
    "epic chill": "Ambient", "new age": "Ambient",
    "chillout": "Downtempo", "chill out": "Downtempo", "chill": "Downtempo", "lounge": "Downtempo",
    "psychedelic": "Psybient", "psychill": "Psybient", "psy chill": "Psybient",
    "russian hip hop": "Russian Rap",
    "game music": "Games", "video game": "Games", "video game music": "Games", "vgm": "Games",
    "музыка из игр": "Games",
}
for _en, _ru in RUSSIAN.items():                        # every Russian name of the tree is an alias of its genre
    _ALIASES.setdefault(_ru, _en)
# not genres at all: dropped
_JUNK = {"other", "others", "misc", "unknown", "genre", "music", "мусор", "другое", "разное", "без жанра", "none",
         "roblox audio"}
_SITE = re.compile(r"\.(com|net|org|ru|cc|fm|me|info|biz|su|tv|io)\b", re.I)
# several genres glued into one tag: «Post-Hardcore | Pop-Punk», «Ambient, Psybient», «Folk / Rock»
_GLUED = re.compile(r"\s*[|;,\n]\s*|\s+/\s+")


def _key(name: str) -> str:
    s = name.casefold().replace("ё", "е").replace("&", " and ")
    s = re.sub(r"[\-_./,'’]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


_CANON: dict[str, tuple[str, ...]] = {}
for _fam, _subs in GENRE_TREE.items():
    for _g in (_fam, *_subs):
        _CANON[_key(_g)] = (_g,)
for _alias, _target in _ALIASES.items():
    _CANON.setdefault(_key(_alias), _target if isinstance(_target, tuple) else (_target,))


_CYRILLIC = re.compile(r"[\u0400-\u04FF]")
LATIN_ONLY = "Жанры пишутся по-английски (латиницей)"


def has_cyrillic(text: str) -> bool:
    return bool(_CYRILLIC.search(text or ""))


def check_latin(genres) -> None:
    """Typed genres must be English: ValueError naming the Russian ones («Поп-панк» -> write «Pop Punk»)."""
    items = [genres] if isinstance(genres, str) else list(genres or [])
    bad = [g for g in items if has_cyrillic(str(g))]
    if bad:
        hint = [f"{b} → {canonical(str(b))[-1]}" for b in bad if "/" not in str(b) and canonical(str(b))]
        raise ValueError(f"{LATIN_ONLY}: {', '.join(map(str, bad))}" + (f" (например: {'; '.join(hint)})" if hint else ""))


def split_glued(tag: str) -> list[str]:
    """«Post-Hardcore | Pop-Punk» -> ["Post-Hardcore", "Pop-Punk"] (a path «Punk/Pop Punk» is not split here)."""
    return [p for p in _GLUED.split(tag or "") if p.strip()]


def canonical(name: str) -> list[str]:
    """The canonical genre name(s) for a tag in any language/spelling; [] for junk.  An unknown genre is kept."""
    name = (name or "").strip()
    if not name:
        return []
    pieces = split_glued(name)
    if len(pieces) > 1:                                 # «Post-Hardcore | Pop-Punk»: every piece
        return [c for p in pieces for c in canonical(p)]
    k = _key(name)
    if not k or k in _JUNK or _SITE.search(name):
        return []
    if k in _CANON:
        return list(_CANON[k])
    if "/" in name:                                     # a path «Punk/Pop Punk» or a file tag «Rock/Pop»
        return [c for p in name.split("/") if p.strip() for c in canonical(p)]
    if has_cyrillic(name):                              # an unknown Russian name is never stored: genres are English
        return []
    return [name[0].upper() + name[1:]]


def family_of(name: str) -> str | None:
    return PARENT.get(name)


_PATH_SEP = re.compile(r"[,;\n]+")


def to_paths(genres) -> str:
    """A track's genres as hierarchical text: «Punk/Pop Punk, Metal» (a family with a sub-genre is written once,
    as the path to the sub-genre)."""
    gs = list(genres or [])
    has_sub = {PARENT[g] for g in gs if g in PARENT}
    out = []
    for g in gs:
        if g in PARENT:
            out.append(f"{PARENT[g]}/{g}")
        elif g not in has_sub:
            out.append(g)
    return ", ".join(out)


def from_paths(text: str) -> list[str]:
    """«Punk/Pop Punk, Metal/Djent; Folk» -> every genre on the paths (the catalogue keeps family and sub-genre)."""
    out: list[str] = []
    seen: set[str] = set()
    for item in _PATH_SEP.split(text or ""):
        for part in item.split("/"):
            part = part.strip()
            same = (canonical(part) or [part])[0].casefold()      # «Панк» after «Punk» is the same genre
            if part and same not in seen:
                seen.add(same)
                out.append(part)
    return out


DEFAULT_COLOUR = "#C8CDD3"


def _add_to_tree(name: str, parent: str | None) -> bool:
    """Put a user's genre into the shared tree (in place: every module that imported GENRE_TREE / PARENT /
    GENRE_CHIPS sees it).  False when it is already known."""
    if name in PARENT or name in GENRE_TREE:
        return False
    if parent:
        GENRE_TREE.setdefault(parent, []).append(name)
        PARENT[name] = parent
    else:
        GENRE_TREE[name] = []
    _CANON[_key(name)] = (name,)                       # the user's own genre wins over an alias («Sludge» -> Metal)
    _CUSTOM_KEYS.add(_key(name))
    GENRE_CHIPS[:] = [g for fam, subs in GENRE_TREE.items() for g in [fam, *subs]]
    return True


_BUILTIN = {fam: list(subs) for fam, subs in GENRE_TREE.items()}
_BUILTIN_CANON = dict(_CANON)
_CUSTOM_KEYS: set[str] = set()


def _reset_tree() -> None:
    _CANON.clear()
    _CANON.update(_BUILTIN_CANON)                       # aliases a custom genre had overridden come back
    _CUSTOM_KEYS.clear()
    GENRE_TREE.clear()
    GENRE_TREE.update({fam: list(subs) for fam, subs in _BUILTIN.items()})
    PARENT.clear()
    PARENT.update({sub: fam for fam, subs in GENRE_TREE.items() for sub in subs})
    GENRE_CHIPS[:] = [g for fam, subs in GENRE_TREE.items() for g in [fam, *subs]]


def load_custom(db) -> None:
    """The user's own families and sub-genres (table custom_genres) join the tree when the catalogue opens (the tree
    is first reset to the built-in one: another catalogue's genres do not leak in)."""
    _reset_tree()
    try:
        rows = db.conn.execute("SELECT name, parent FROM custom_genres ORDER BY added").fetchall()
    except Exception:  # noqa: BLE001 - an old catalogue without the table
        return
    for name, parent in sorted(rows, key=lambda r: r[1] is not None):          # families first
        _add_to_tree(name, parent)


def learn(db, text: str) -> list[str]:
    """«Punk/Skate Punk, Chanson»: genres the tree does not know become its sub-genres / families (kept in the
    catalogue, so they get buttons everywhere).  Returns the newly learned names."""
    import time
    check_latin(_PATH_SEP.split(text or ""))
    new: list[str] = []
    for item in _PATH_SEP.split(text or ""):
        raw = [p.strip() for p in item.split("/") if p.strip()]
        parts = [(canonical(p) or [None])[0] for p in raw]
        if len(parts) > 1 and parts[1] and (parts[1] in GENRE_TREE or parts[1] == parts[0]):
            # «Metal/Sludge»: «Sludge» is only an alias of the family, the user asks for a sub-genre of its own
            parts[1] = raw[1][0].upper() + raw[1][1:]
        parts = [p for p in parts if p]
        if not parts:
            continue
        fam = parts[0]
        if fam in PARENT:                               # «Alternative/…»: a known sub-genre heads the path
            fam = PARENT[fam]
        elif fam not in GENRE_TREE and _add_to_tree(fam, None):
            db.conn.execute("INSERT OR IGNORE INTO custom_genres VALUES(?,?,?)", (fam, None, time.time()))
            new.append(fam)
        for sub in parts[1:2]:                          # two levels: family / sub-genre
            if sub not in PARENT and sub not in GENRE_TREE and _add_to_tree(sub, fam):
                db.conn.execute("INSERT OR IGNORE INTO custom_genres VALUES(?,?,?)", (sub, fam, time.time()))
                new.append(sub)
    if new:
        db.conn.commit()
    return new


def path_suggestions(existing: list[str] | None = None) -> list[str]:
    """What the genre input offers: the tree as paths («Rock», «Rock/Alternative» ...) and the library's own genres
    outside the tree."""
    out = [p for fam, subs in GENRE_TREE.items() for p in [fam, *(f"{fam}/{s}" for s in subs)]]
    known = {g.casefold() for g in GENRE_CHIPS}
    out += [g for g in existing or [] if g and g.casefold() not in known]
    return out


def normalize(genres) -> list[str]:
    """The catalogue form of a genre list: canonical names, no duplicates, every sub-genre preceded by its family."""
    out: list[str] = []
    seen: set[str] = set()

    def add(g: str) -> None:
        if g.casefold() not in seen:
            seen.add(g.casefold())
            out.append(g)
    for raw in genres or []:
        for g in canonical(raw):
            fam = PARENT.get(g)
            if fam:
                add(fam)
            add(g)
    return out


def search_text(genres: list[str]) -> str:
    """Genres as indexed for full-text search: the canonical (English) name and the Russian one."""
    return " ".join(f"{g} {RUSSIAN[g]}" if g in RUSSIAN else g for g in genres)


def era_label(decade: int) -> str:
    return f"{decade}-е"
