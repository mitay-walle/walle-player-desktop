"""Likes imported from a file into a listener: the formats, tolerant matching, owner vs guest."""

import json

from musicplayer.core import likeimport


def _lib(db):
    ids = {}
    for path, title, artist in [
        (r"E:\m\Кино\01 - Группа крови.mp3", "Группа крови", "Кино"),
        (r"E:\m\Кино\02 - Звезда по имени Солнце.mp3", "Звезда по имени Солнце", "Кино"),
        (r"E:\m\Noize MC\Всё как у людей.mp3", "Всё как у людей", "Noize MC"),
        (r"E:\m\Beatles\Yesterday.mp3", "Yesterday (Remastered 2009)", "The Beatles"),
        (r"E:\m\Linkin Park\Numb.mp3", "Numb", "Linkin Park"),
        (r"E:\m\Other\Numb.mp3", "Numb", "U2"),
        (r"E:\m\Imagine Dragons\Believer.mp3", "Believer", "Imagine Dragons feat. Lil Wayne"),
    ]:
        ids[title] = db.upsert_track({"path": path, "title": title, "artist": artist}, [])
    db.conn.commit()
    return ids


def test_formats_are_read():
    text = "1. Кино - Группа крови\n2) Noize MC — Всё как у людей\n\n# a comment\nYesterday\n"
    assert likeimport.parse(text, "likes.txt") == [
        {"artist": "Кино", "title": "Группа крови"}, {"artist": "Noize MC", "title": "Всё как у людей"},
        {"artist": "", "title": "Yesterday"}]
    csv_text = "Track Name,Artist Name(s),Album Name\nNumb,Linkin Park,Meteora\n\"Believer\",Imagine Dragons,Evolve\n"
    assert likeimport.parse(csv_text, "spotify.csv") == [{"artist": "Linkin Park", "title": "Numb"},
                                                         {"artist": "Imagine Dragons", "title": "Believer"}]
    tsv = "исполнитель\tназвание\nКино\tЗвезда по имени Солнце\n"
    assert likeimport.parse(tsv, "x.tsv") == [{"artist": "Кино", "title": "Звезда по имени Солнце"}]
    yandex = {"tracks": [{"title": "Numb", "artists": [{"name": "Linkin Park"}]}, "Кино - Группа крови",
                         {"track": {"name": "Believer", "artists": [{"name": "Imagine Dragons"}]}}]}
    assert likeimport.parse(json.dumps(yandex, ensure_ascii=False), "y.json") == [
        {"artist": "Linkin Park", "title": "Numb"}, {"artist": "Кино", "title": "Группа крови"},
        {"artist": "Imagine Dragons", "title": "Believer"}]
    m3u = "#EXTM3U\n#EXTINF:215,Кино - Группа крови\nE:\\m\\Кино\\01 - Группа крови.mp3\nD:\\elsewhere\\Numb.mp3\n"
    items = likeimport.parse(m3u, "pl.m3u8")
    assert items[0] == {"artist": "Кино", "title": "Группа крови", "path": "E:\\m\\Кино\\01 - Группа крови.mp3"}
    assert items[1]["path"].endswith("Numb.mp3")


def test_tolerant_matching(db):
    ids = _lib(db)
    items = likeimport.parse(
        "КИНО — группа крови\n"                       # case
        "Noize MC - Все как у людей\n"                # ё written as е
        "Beatles - Yesterday\n"                       # «(Remastered 2009)» and «The» do not matter
        "Imagine Dragons - Believer (feat. Lil Wayne)\n"
        "Кино - Звезда по имени Солнца\n"             # a typo
        "Numb\n"                                      # a bare title of two different songs: not guessed
        "Linkin Park - Numb\n"
        "Кино - Песня без слов\n", "x.txt")           # not in the library
    res = likeimport.match(db, items)
    got = {likeimport.describe(i): t["id"] for i, t in res["matched"]}
    assert got == {"КИНО — группа крови": ids["Группа крови"], "Noize MC — Все как у людей": ids["Всё как у людей"],
                   "Beatles — Yesterday": ids["Yesterday (Remastered 2009)"],
                   "Imagine Dragons — Believer (feat. Lil Wayne)": ids["Believer"],
                   "Кино — Звезда по имени Солнца": ids["Звезда по имени Солнце"],
                   "Linkin Park — Numb": db.conn.execute("SELECT id FROM tracks WHERE artist='Linkin Park'").fetchone()[0]}
    assert [likeimport.describe(i) for i in res["unmatched"]] == ["? — Numb", "Кино — Песня без слов"]


def test_likes_go_to_the_chosen_listener(db):
    ids = _lib(db)
    owner, guest = {"id": "owner", "owner": True}, {"id": "u1", "owner": False}
    two = [ids["Numb"], ids["Группа крови"]]
    assert likeimport.apply(db, owner, two) == 2
    assert set(db.favorites("track")) == {str(i) for i in two}
    assert db.favorites("track")[0] == str(ids["Numb"])           # the file's first line is the newest like
    assert likeimport.apply(db, owner, two) == 0                  # already liked: nothing new
    assert likeimport.apply(db, guest, [ids["Believer"]]) == 1
    assert [r[0] for r in db.conn.execute("SELECT key FROM remote_favorites WHERE user='u1'")] == [str(ids["Believer"])]
    assert str(ids["Believer"]) not in db.favorites("track")      # a guest's likes never touch the owner's


def test_file_encodings(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes("Кино - Группа крови".encode("cp1251"))
    assert likeimport.read_file(p) == "Кино - Группа крови"
    p.write_bytes("Кино - Группа крови".encode("utf-16"))
    assert likeimport.read_file(p) == "Кино - Группа крови"


def test_import_dialog(qtbot, tmp_path):
    from musicplayer.ui.context import AppContext
    from musicplayer.ui.likeimport_dialog import LikeImportDialog
    ctx = AppContext(tmp_path / "home")
    try:
        ids = _lib(ctx.db)
        f = tmp_path / "likes.txt"
        f.write_text("Linkin Park - Numb\nКино - Песня без слов\n", encoding="utf-8")
        got = []
        ctx.events.favorites_changed.connect(lambda: got.append(True))
        dlg = LikeImportDialog(ctx, {"id": "owner", "name": "Я", "owner": True}, str(f))
        qtbot.addWidget(dlg)
        lp = ctx.db.conn.execute("SELECT id FROM tracks WHERE artist='Linkin Park'").fetchone()[0]
        assert dlg.track_ids == [lp] and dlg.unmatched == ["Кино — Песня без слов"]
        assert dlg.ok.isDefault()                                 # Enter imports
        dlg.ok.click()
        assert dlg.count == 1 and got and ctx.db.favorites("track") == [str(lp)]
    finally:
        ctx.close()


def test_like_dates_from_exports_keep_the_order(db):
    """A date in the export becomes the like's time (sortable); without one the file's order is kept."""
    from musicplayer.core import likes
    _lib(db)
    csv_text = ("Track Name,Artist Name(s),Added At\n"
                "Numb,Linkin Park,2021-03-05T10:00:00Z\n"
                "Believer,Imagine Dragons,2023-07-01T08:30:00Z\n")
    items = likeimport.parse(csv_text, "spotify.csv")
    assert items[0]["added"] == 1614938400.0 and items[1]["added"] > items[0]["added"]
    yandex = '{"tracks": [{"timestamp": "2020-01-02T03:04:05+03:00", "track": {"title": "Группа крови", "artists": [{"name": "Кино"}]}}]}'
    y = likeimport.parse(yandex, "y.json")
    assert y[0]["artist"] == "Кино" and y[0]["added"] == 1577923445.0
    res = likeimport.match(db, items + y)
    likeimport.apply(db, {"id": "owner", "owner": True}, [t["id"] for _, t in res["matched"]],
                     [i.get("added") for i, _ in res["matched"]])
    order = [db.get_track(int(k))["title"] for k in likes.keys(db, "owner", "track")]
    assert order == ["Believer", "Numb", "Группа крови"]                       # newest first, by the export's dates
    assert likeimport.parse_time("1614938400000") == 1614938400.0             # milliseconds
    assert likeimport.parse_time("05.03.2021") is not None and likeimport.parse_time("вчера") is None
