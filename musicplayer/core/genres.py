"""Genre buttons for labelling, as a small hierarchy: 7 families, each with a few sub-genres (about 20 in all).

Genres are free text in the catalogue (track_genres); a button is lit when the track has that genre (case-insensitive).
Choosing a sub-genre also sets its family; the sub-genres of a family are shown once the family is lit."""

from __future__ import annotations

GENRE_TREE: dict[str, list[str]] = {
    "Rock": ["Alternative", "Grunge", "Post-punk"],
    "Punk": ["Pop punk", "Hardcore", "Emocore"],
    "Metal": ["Metalcore", "Post-hardcore", "Djent", "Power metal", "Nu metal"],
    "Electronic": ["Techno", "Drum and bass", "Synthwave", "8-bit", "Ambient"],
    "Hip-hop": ["Русский рэп", "Grime"],
    "Pop": [],
    "Jazz": [],
    "Soundtrack": ["Games"],
    "Oriental": [],
}
PARENT = {sub: fam for fam, subs in GENRE_TREE.items() for sub in subs}
GENRE_CHIPS = [g for fam, subs in GENRE_TREE.items() for g in [fam, *subs]]
ERAS = [1960, 1970, 1980, 1990, 2000, 2010, 2020]


def era_label(decade: int) -> str:
    return f"{decade}-е"
