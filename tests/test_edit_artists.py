from musicplayer.core.scanner import scan


def test_edit_track_survives_rescan_and_artist_overview(db, library, tmp_path):
    scan(db, library, tmp_path / "c")
    t = db.search(limit=1)[0]
    db.edit_track([t["id"]], {"title": "Правильное название", "artist": "Правильный исполнитель feat. Гость", "year": "1999"})
    got = db.get_track(t["id"])
    assert (got["title"], got["artist"], got["year"]) == ("Правильное название", "Правильный исполнитель feat. Гость", 1999)
    assert got["feat"] == ["Гость"]
    assert db.search("Правильное", limit=5)[0]["id"] == t["id"]          # search index updated
    scan(db, library, tmp_path / "c")                                    # a rescan keeps the correction
    assert db.get_track(t["id"])["title"] == "Правильное название"
    db.edit_track([t["id"]], {"title": ""})                              # empty: back to the file's tag on rescan
    scan(db, library, tmp_path / "c")
    assert db.get_track(t["id"])["title"] != "Правильное название"
    over = db.artists_overview()
    assert over and {"artist", "tracks", "albums", "genres", "moods", "voice"} <= set(over[0])
    assert sum(r["tracks"] for r in over) == db.count()
