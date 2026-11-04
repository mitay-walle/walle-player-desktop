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
    assert sum(r["tracks"] for r in over) == db.count() + 1             # the featured guest is listed too
    assert next(r for r in over if r["artist"] == "Гость")["tracks"] == 1


def test_duplicate_artists_are_found_and_merged(db):
    from musicplayer.core import artistdupes, likes
    a = [db.upsert_track({"path": f"a{i}.mp3", "title": f"a{i}", "artist": "Story of the Year"}, []) for i in range(3)]
    b = [db.upsert_track({"path": f"b{i}.mp3", "title": f"b{i}", "artist": "STORY OF THE YEAR",
                          "albumartist": "Story Of The Year"}, []) for i in range(2)]
    c = db.upsert_track({"path": "c.mp3", "title": "c", "artist": "Мёртвые дельфины"}, [])
    d = db.upsert_track({"path": "d.mp3", "title": "d", "artist": "Мертвые Дельфины"}, [])
    db.upsert_track({"path": "e.mp3", "title": "e", "artist": "Other"}, [])
    assert artistdupes.key("The Misfits") == artistdupes.key("Misfits")
    assert artistdupes.key("Angels & Airwaves") == artistdupes.key("Angels And Airwaves")
    groups = artistdupes.find(db)
    assert [g["best"] for g in groups] == ["Story of the Year", "Мертвые Дельфины"] or \
        [g["best"] for g in groups] == ["Story of the Year", "Мёртвые дельфины"]
    likes.set_liked(db, "owner", "artist", "STORY OF THE YEAR", True)
    g = groups[0]
    assert artistdupes.merge(db, [x["name"] for x in g["names"]], "Story of the Year") == 2
    assert {db.get_track(t)["artist"] for t in a + b} == {"Story of the Year"}
    assert {db.get_track(t)["albumartist"] for t in b} == {"Story of the Year"}
    assert likes.is_liked(db, "owner", "artist", "Story of the Year")
    assert not likes.is_liked(db, "owner", "artist", "STORY OF THE YEAR")
    artistdupes.merge(db, ["Мёртвые дельфины", "Мертвые Дельфины"], "Мёртвые дельфины")
    assert db.get_track(d)["artist"] == "Мёртвые дельфины" == db.get_track(c)["artist"]
    assert artistdupes.find(db) == []
