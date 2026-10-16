"""Titles whose Cyrillic letters became «�» are read back from a tracklist by their mask."""

from musicplayer.core.titlefix import fits, recover_artist, recover_title

B = "�"


def t(pos, title, names=("Порнофильмы",)):
    return {"position": pos, "title": title, "names": list(names), "credit": " feat. ".join(names)}


TRACKS = [t(1, "Надежда"), t(2, "Система"), t(3, "Проснись!"), t(4, "Передай другому", ("Порнофильмы", "Элизиум")),
          t(5, "I Think It’s Going to Rain Today")]


def test_mask_fits_length_spaces_and_latin():
    assert fits(B * 8 + "!", "Проснись!")
    assert not fits(B * 9, "Проснись!")
    assert fits(f"I Think It{B}s Going To Rain Today", "I Think It’s Going to Rain Today")   # case, ’
    assert not fits(B * 3, "Abc")                                                        # «�» is never ASCII


def test_same_length_titles_told_apart_by_track_number():
    assert recover_title(B * 7, TRACKS, 2)[0] == "Система"
    assert recover_title(B * 7, TRACKS, None)[0] is None                                 # ambiguous: not guessed


def test_tail_feat_names_from_credit_or_dropped():
    assert recover_title(f"{B * 7} {B * 7} (feat. {B * 7})", TRACKS, 4)[0] == "Передай другому (feat. Элизиум)"
    assert recover_title(f"{B * 7} {B * 7} (feat. {B * 3} {B * 4})", TRACKS, 4)[0] == "Передай другому"


def test_artist_from_folder_and_credit():
    assert recover_artist(B * 11, "Порнофильмы", None) == "Порнофильмы"
    assert recover_artist(f"{B * 11} feat. {B * 7}", "Порнофильмы", TRACKS[3]) == "Порнофильмы feat. Элизиум"
