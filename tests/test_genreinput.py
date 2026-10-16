"""Genres typed as hierarchical paths «Семейство/Поджанр» with suggestions."""

from musicplayer.core.genres import from_paths, normalize, path_suggestions, to_paths
from tests.test_remote import client, lib  # noqa: F401  (server fixtures)


def test_paths_round_trip():
    assert to_paths(["Punk", "Pop Punk", "Metal"]) == "Punk/Pop Punk, Metal"
    assert to_paths(["Metal", "Djent", "Metalcore", "Свой жанр"]) == "Metal/Djent, Metal/Metalcore, Свой жанр"
    assert from_paths("Punk/Pop Punk, Metal/Djent; Folk\nПанк") == ["Punk", "Pop Punk", "Metal", "Djent", "Folk"]
    assert normalize(from_paths(to_paths(["Punk", "Pop Punk", "Metal"]))) == ["Punk", "Pop Punk", "Metal"]
    assert normalize(from_paths("pop punk")) == ["Punk", "Pop Punk"]                 # any spelling still works


def test_suggestions_are_the_tree_and_own_genres():
    s = path_suggestions(["Rock", "Chanson", "Pop Punk"])
    assert s[:2] == ["Rock", "Rock/Alternative"] and "Punk/Pop Punk" in s
    assert s[-1] == "Chanson" and s.count("Rock") == 1                                # own genres outside the tree


def test_input_suggests_for_the_item_being_typed(qtbot):
    from musicplayer.ui.genreinput import GenreLineEdit
    w = GenreLineEdit()
    qtbot.addWidget(w)
    w.set_known(["Chanson"])
    w.setText("Metal/Djent, punk")
    w.setCursorPosition(len(w.text()))
    w._suggest(w.text())
    shown = [w.comp.completionModel().index(i, 0).data() for i in range(w.comp.completionCount())]
    assert {"Punk", "Punk/Pop Punk", "Rock/Post-Punk"} <= set(shown) and "Metal/Djent" not in shown   # contains, any level
    w._insert("Punk/Pop Punk")
    assert w.text() == "Metal/Djent, Punk/Pop Punk"
    w.setText("chan")
    w.setCursorPosition(4)
    w._suggest(w.text())
    assert w.comp.currentCompletion() == "Chanson"


def test_new_genres_join_the_tree_with_buttons(qtbot, tmp_path):
    from musicplayer.core import genres
    from musicplayer.core.db import Database
    from musicplayer.ui.pages.library import GenreChips
    db = Database(tmp_path / "a.db")
    chips = GenreChips()
    qtbot.addWidget(chips)
    assert "Crust Punk" not in chips.buttons
    assert genres.learn(db, "Punk/Crust Punk, Chanson, Rock/Alternative") == ["Crust Punk", "Chanson"]
    assert genres.PARENT["Crust Punk"] == "Punk" and "Chanson" in genres.GENRE_TREE
    assert genres.normalize(["crust punk"]) == ["Punk", "Crust Punk"]           # the sub-genre brings its family
    rebuilt = []
    chips.rebuilt.connect(lambda: rebuilt.append(True))
    chips.show_state([{"genres": ["Punk", "Crust Punk"]}])
    assert rebuilt and chips.buttons["Crust Punk"].isChecked() and "Chanson" in chips.buttons
    assert genres.FAMILY_COLOURS["Punk"] in chips.buttons["Crust Punk"].styleSheet()       # in its family's colour
    db.close()
    other = Database(tmp_path / "b.db")                                       # another catalogue: not its genres
    assert "Crust Punk" not in genres.PARENT and "Chanson" not in genres.GENRE_TREE
    other.close()
    again = Database(tmp_path / "a.db")                                       # reopened: they come back
    assert genres.PARENT.get("Crust Punk") == "Punk"
    again.close()
    Database()                                                                # leave the built-in tree to the next tests


def test_server_learns_paths_from_a_client(lib, client):
    from tests.test_remote import AUTH
    tid = lib.ids[0]
    r = client.post(f"/api/tracks/{tid}/labels", json={"genres": ["Metal/Doom Metal"]}, headers=AUTH)
    assert r.status_code == 200 and r.json()["genres"] == ["Metal", "Doom Metal"]
    tree = {f["name"]: f["subs"] for f in client.get("/api/info", headers=AUTH).json()["labels"]["genre_tree"]}
    assert "Doom Metal" in tree["Metal"]
    from musicplayer.core.db import Database
    Database()                                                                # reset the shared tree

def test_english_path_whose_sub_genre_is_a_family_alias(lib, client):
    """«Metal/Sludge»: «sludge» is an alias of Metal, but on a path the user asks for a sub-genre of its own."""
    from musicplayer.core import genres
    from musicplayer.core.db import Database
    from tests.test_remote import AUTH
    tid = lib.ids[0]
    for sent in (["Metal/Sludge"], ["Metal", "Metal/Sludge"]):
        r = client.post(f"/api/tracks/{tid}/genres", json={"genres": sent}, headers=AUTH)
        assert r.status_code == 200 and r.json()["genres"] == ["Metal", "Sludge"], sent
    assert genres.PARENT["Sludge"] == "Metal"
    Database()                                                                # reset the shared tree
    assert genres.normalize(["Sludge"]) == ["Metal"]                          # the built-in alias is back
