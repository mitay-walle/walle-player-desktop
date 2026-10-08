"""Recognise featured artists in titles and artist tags ("(feat. A, B)", "ft. A", "при участии A", "X feat Y")."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

KEY = r"(?:feat(?:uring)?\b\.?|ft\b\.?|при\s+участии|при\s+уч\.|с\s+уч\.)"
# keyword preceded by "(" / "[" (then the group runs to the matching bracket) or by whitespace/start
_BRACKETED = re.compile(r"[(\[]\s*" + KEY + r"\s*", re.I)
_PLAIN = re.compile(r"(?:^|\s)" + KEY + r"\s+", re.I)
_SPLIT = re.compile(r"\s*[,;]\s*")
_CLOSE = {"(": ")", "[": "]"}


@dataclass
class Parsed:
    title: str                                  # title without the feat part
    artists: list[str] = field(default_factory=list)   # main artists (from the artist tag)
    feat: list[str] = field(default_factory=list)      # featured artists


def _take_bracket(s: str, start: int) -> tuple[str, int]:
    """Text from ``start`` to the bracket that closes the one opened right before it (nesting aware)."""
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


def _names(text: str) -> list[str]:
    out = []
    for n in _SPLIT.split(text.strip(" .")):
        n = n.strip(" .")
        if n and n.lower() not in (x.lower() for x in out):
            out.append(n)
    return out


def parse(title: str | None, artist: str | None = None) -> Parsed:
    title, artist = title or "", artist or ""
    feat: list[str] = []
    clean = title
    m = _BRACKETED.search(clean)
    if m:
        body, end = _take_bracket(clean, m.end())
        feat += _names(body)
        clean = (clean[:m.start()] + " " + clean[end:]).strip()
    else:
        m = _PLAIN.search(clean)
        if m:
            feat += _names(clean[m.end():])
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
