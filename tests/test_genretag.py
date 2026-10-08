from musicplayer.core.genretag import map_tags


def test_map_tags_to_tree():
    assert map_tags([["post-hardcore", 5], ["metalcore", 4], ["rock", 1]]) == ["Metal", "Post-hardcore", "Metalcore"]
    assert map_tags([["hip hop", 3]], lang="ru") == ["Hip-hop", "Русский рэп"]
    assert map_tags([["pop punk", 2], ["punk rock", 2]])[:2] == ["Punk", "Pop punk"]
    assert map_tags([["emotional", 3], ["indie rock", 3]]) == ["Rock"]
    assert map_tags([]) == []


def test_games_and_oriental():
    assert map_tags([["video game music", 3]]) == ["Soundtrack", "Games"]
    assert map_tags([["oriental", 2]]) == ["Oriental"]


def test_chiptune():
    assert map_tags([["chiptune", 3]]) == ["Electronic", "8-bit"]


def test_jazz():
    assert map_tags([["vocal jazz", 4], ["swing", 2]]) == ["Jazz"]
