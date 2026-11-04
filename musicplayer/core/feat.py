"""Recognise featured artists in titles and artist tags ("(feat. A, B)", "ft. A", "(with A)", "при участии A", "X feat Y"),
and split a collaboration written in the artist tag ("A, B", "A & B", "A и B", "A vs. B") into its artists."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

KEY = r"(?:feat(?:uring)?\b\.?|ft\b\.?|при\s+участии|при\s+уч\.|с\s+уч\.)"
# keyword preceded by "(" / "[" / a quote (then the group runs to the matching bracket) or by whitespace/start;
# «(with X)» counts only inside brackets: «Mamacita with Burrito» is a title
_BRACKETED = re.compile(r"[(\[\"«]\s*(?P<kw>" + KEY + r"|with\b)\s*", re.I)
_PLAIN = re.compile(r"(?:^|\s)" + KEY + r"\s+", re.I)
_SPLIT = re.compile(r"\s*(?:[,;]|\s/\s|\s&\s|\s\+\s)\s*")
_CLOSE = {"(": ")", "[": "]", '"': '"', "«": "»"}
_NOT_NAMES = {"you", "me", "us", "him", "her", "them", "it", "love", "friends", "a friend", "orchestra", "strings",
              "band", "choir", "piano", "guitar", "nobody", "everyone", "anyone", "someone", "god", "fire", "tears",
              "pride", "time", "no one", "the band", "the orchestra"}
_JUNK_NAME = re.compile(r"(?i)(?:demo|live|remix|bonus|version|cover|edit|mix|original|instrumental)\b")
_TAIL_WORD = re.compile(r"(?i)\s+(?:cover|remix|mix|live|acoustic|version|edit|ремикс|кавер)$")
_TAIL_BRACKET = re.compile(r"(?i)\s*[(\[](?:live|remix|mix|version|demo|cover|edit|acoustic|bonus|ремикс)[^)\]]*[)\]]?\s*$")
_CYR = re.compile(r"[Ѐ-ӿ]")


@dataclass
class Parsed:
    title: str                                  # title without the feat part
    artists: list[str] = field(default_factory=list)   # main artists (from the artist tag)
    feat: list[str] = field(default_factory=list)      # featured artists


def fix_mojibake(s: str) -> str:
    """«Ñåðãåé ×ÈÆ» (a Windows-1251 tag read as Windows-1252) -> «Сергей ЧИЖ»; anything else is returned as it was."""
    if not s or _CYR.search(s) or sum(ord(ch) > 127 for ch in s) < 3:
        return s
    try:
        raw = s.encode("cp1252")
    except UnicodeEncodeError:
        try:
            raw = s.encode("latin-1")
        except UnicodeEncodeError:
            return s
    try:
        fixed = raw.decode("cp1251")
    except UnicodeDecodeError:
        return s
    if not _CYR.search(fixed):
        return s
    for w in re.findall(r"\w+", fixed):             # «Beyoncé» -> «Beyoncй»: a word of two alphabets is not a repair
        if re.search(r"[A-Za-z]", w) and _CYR.search(w):
            return s
    return fixed


def _take_group(s: str, start: int, opener: str) -> tuple[str, int]:
    """Text from ``start`` to the bracket / quote that closes the one opened right before it (nesting aware)."""
    if opener in "\"«":
        close = _CLOSE[opener]
        i = s.find(close, start)
        return (s[start:], len(s)) if i < 0 else (s[start:i], i + 1)
    depth, i = 1, start
    while i < len(s):
        if s[i] in "([":
            depth += 1
        elif s[i] in ")]":
            depth -= 1
            if depth == 0:
                return s[start:i], i + 1
        i += 1
    return s[start:], len(s)


def _depth_at(s: str, pos: int) -> int:
    d = 0
    for ch in s[:pos]:
        if ch in "([":
            d += 1
        elif ch in ")]" and d > 0:
            d -= 1
    return d


def _clean_name(n: str) -> str:
    n = fix_mojibake(n.strip(" .\"«»"))
    n = re.sub(r"\s+-\s+.*$", "", n)                   # «Name - Song»: the song is not the artist
    n = _TAIL_BRACKET.sub("", n)
    n = _TAIL_WORD.sub("", n)
    n = n.strip(" .,;")
    if not n or n.isdigit() or _JUNK_NAME.match(n) or (len(n) < 2 and not n.isalpha()):   # «N» is an artist
        return ""
    return n


def _names(text: str) -> list[str]:
    out: list[str] = []
    for n in _SPLIT.split(text.strip(" .")):
        n = _clean_name(n)
        if n and n.lower() not in (x.lower() for x in out):
            out.append(n)
    return out


def _unbalanced_head(s: str) -> str:
    """``s`` up to a closing bracket that was never opened in it ("Kimbra cover)" -> "Kimbra cover")."""
    d = 0
    for i, ch in enumerate(s):
        if ch in "([":
            d += 1
        elif ch in ")]":
            if d == 0:
                return s[:i]
            d -= 1
    return s


def parse(title: str | None, artist: str | None = None) -> Parsed:
    title, artist = title or "", artist or ""
    feat: list[str] = []
    clean = title
    for _ in range(3):                                   # «Song (feat. A) (with B)»
        m = _BRACKETED.search(clean)
        if not m:
            break
        opener = m.group(0)[0]
        body, end = _take_group(clean, m.end(), opener)
        names = _names(body)
        if m.group("kw").lower() == "with":
            names = [n for n in names if n[0].isupper() and n.casefold() not in _NOT_NAMES]
        feat += [n for n in names if n.lower() not in (x.lower() for x in feat)]
        clean = (clean[:m.start()] + " " + clean[end:]).strip()
    if not feat:
        m = _PLAIN.search(clean)
        if m:
            tail = clean[m.end():]
            if _depth_at(clean, m.start()):               # «Song (Gotye feat Kimbra cover)»: only the feat is cut out
                body = _unbalanced_head(tail)
                feat += _names(body)
                clean = (clean[:m.start()] + clean[m.end() + len(body):]).strip()
            else:
                feat += _names(tail)
                clean = clean[:m.start()].strip()
    clean = re.sub(r"\s+", " ", clean).strip(" -–")
    mains = [artist] if artist else []
    m = _PLAIN.search(artist) or re.search(r"\s" + KEY + r"\s*", artist, re.I)
    if m:
        for n in _names(artist[m.end():]):
            if n.lower() not in (x.lower() for x in feat):
                feat.append(n)
        mains = [artist[:m.start()].strip()] if artist[:m.start()].strip() else []
    return Parsed(clean or title, mains, feat)


def feat_names(title: str | None, artist: str | None = None) -> list[str]:
    return parse(title, artist).feat


# ---- collaborations written in the artist tag --------------------------------------------------------------
_COLLAB = re.compile(r"\s*(?:[,;]|\s/\s|/|\s&\s|&|\s\+\s|\s(?:и|and|x|х|vs\.?|with)\s)\s*", re.I)
# well-known acts with the separator in their name (the library check in core/db.py keeps the rest apart)
NO_SPLIT = {"король и шут", "sleeping with sirens", "earth, wind & fire", "crosby, stills & nash", "hall & oates",
            "simon & garfunkel", "florence + the machine", "years & years", "nico & vinz", "mumford & sons",
            "ac/dc", "belle and sebastian", "iron & wine", "she & him"}


def split_collab(tag: str | None) -> list[str]:
    """The artists of «A, B» / «A & B» / «A и B» / «A vs. B» / «A x B» in order; [tag] when it is one artist.
    A part shorter than two letters or a number («1,5 кг отличного пюре») means it is not a collaboration."""
    tag = (tag or "").strip()
    if not tag or tag.casefold() in NO_SPLIT:
        return [tag] if tag else []
    parts = [p.strip(" .") for p in _COLLAB.split(tag)]
    if len(parts) < 2 or any(len(p) < 2 or p.isdigit() or p.casefold() in ("the", "and", "и") for p in parts):
        return [tag]
    return parts
