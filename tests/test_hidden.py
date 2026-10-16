"""«Не показывать»: per listener, for tracks, albums, artists, genres and labels; lists, search and the wave obey."""

from musicplayer.core import hidden
from musicplayer.core.wave import Wave
from tests.test_app_smoke import app, wait_job  # noqa: F401  (the app fixture)
from tests.test_remote import client, lib  # noqa: F401  (server fixtures)


def _lib(db):
    ids = {}
    for path, title, artist, album, genres in [
        ("a1.mp3", "Группа крови", "Кино", "Группа крови", ["Rock"]),
        ("a2.mp3", "Звезда по имени Солнце", "Кино", "Звезда по имени Солнце", ["Rock"]),
        ("b1.mp3", "Numb", "Linkin Park", "Meteora", ["Metal"]),
        ("c1.mp3", "Believer", "Imagine Dragons", "Evolve", ["Pop"]),
        ("d1.mp3", "Всё как у людей", "Noize MC", "Последний альбом", ["Hip-Hop"]),
    ]:
        ids[title] = db.upsert_track({"path": path, "title": title, "artist": artist, "album": album}, genres)
    db.set_moods([ids["Believer"]], ["Весёлое"], "set")
    db.set_moods([ids["Numb"], ids["Группа крови"]], ["Грустное"], "set")
    db.conn.commit()
    return ids


def titles(rows):
    return sorted(r["title"] for r in rows)


def test_each_kind_hides_for_its_listener_only(db):
    ids = _lib(db)
    everyone = titles(db.search(limit=100))
    hidden.set_hidden(db, "owner", "track", str(ids["Numb"]))
    hidden.set_hidden(db, "owner", "album", hidden.album_key("кино", "ГРУППА КРОВИ"))        # case does not matter
    hidden.set_hidden(db, "owner", "genre", "хип-хоп")
    hidden.set_hidden(db, "owner", "mood", "Весёлое")
    assert titles(db.search(limit=100, hide_user="owner")) == ["Звезда по имени Солнце"]
    assert titles(db.search(limit=100, hide_user="guest1")) == everyone                     # a guest's lists are his
    assert titles(db.search(limit=100)) == everyone                                          # no user: everything
    hidden.set_hidden(db, "guest1", "artist", "КИНО")
    assert "Звезда по имени Солнце" not in titles(db.search(limit=100, hide_user="guest1"))
    hidden.set_hidden(db, "owner", "mood", "Весёлое", False)                                 # «Вернуть»
    assert "Believer" in titles(db.search(limit=100, hide_user="owner"))
    kinds = [(r["kind"], r["title"]) for r in hidden.listing(db, "owner")]
    assert ("track", "Numb") in kinds and ("album", "ГРУППА КРОВИ") in kinds and ("genre", "Hip-Hop") in kinds          # stored under the vocabulary's name
    assert hidden.counts(db, "owner") == {"track": 1, "album": 1, "genre": 1}


def test_wave_and_value_lists_leave_hidden_out(db):
    ids = _lib(db)
    hidden.set_hidden(db, "owner", "artist", "Linkin Park")
    got = {t["id"] for t in Wave(db, ["Грустное"], user="owner").next_batch(10)}
    assert got == {ids["Группа крови"]}
    assert {t["id"] for t in Wave(db, ["Грустное"], user="guest1").next_batch(10)} == {ids["Группа крови"], ids["Numb"]}
    artists = [r["name"] for r in hidden.visible_values(db, "owner", "artist", db.list_values("artist"))]
    assert "Linkin Park" not in artists and "Кино" in artists
    albums = [r["name"] for r in hidden.visible_values(db, "owner", "album", db.list_values("album"))]
    assert "Meteora" not in albums
    hidden.set_hidden(db, "owner", "genre", "Pop")
    assert "Pop" not in [r["name"] for r in hidden.visible_values(db, "owner", "genre", db.list_values("genre"))]


def test_desktop_hide_undo_and_the_hidden_tab(qtbot, app, library):
    from PySide6.QtWidgets import QTreeWidgetItemIterator
    from musicplayer.ui.hideactions import hide
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    lib = win.pages["library"]
    lib.reset_filters()
    artists = lambda: {r["artist"] for r in lib.model.rows}                       # noqa: E731
    assert "Кино" in artists()
    hide(ctx, lib, [("artist", "Кино", "исполнитель «Кино»")])
    assert "Кино" not in artists() and lib.show_hidden.isVisibleTo(lib)
    assert not win._undo_btn.isHidden() and "Кино" in win.statusBar().currentMessage()
    win._undo_btn.click()                                                         # «Отменить»
    assert "Кино" in artists()
    hide(ctx, lib, [("artist", "Кино", "исполнитель «Кино»")])
    lib.show_hidden.setChecked(True)
    assert "Кино" in artists()                                                    # «показать скрытое»
    lib.show_hidden.setChecked(False)
    tab = win.pages["favorites"].hidden
    it = QTreeWidgetItemIterator(tab.tree)
    names = []
    while it.value():
        names.append(it.value().text(0))
        it += 1
    assert "Кино" in names
    leaf = next(tab.tree.topLevelItem(0).child(i) for i in range(tab.tree.topLevelItem(0).childCount()))
    leaf.setSelected(True)
    tab.unhide_selected()                                                         # «Вернуть»
    assert "Кино" in artists() and tab.tree.topLevelItemCount() == 0


def test_api_hidden_is_per_listener(lib, client):
    from tests.test_remote import AUTH, GUEST
    titles_of = lambda headers, q="": sorted(t["title"] for t in client.get(f"/api/tracks{q}", headers=headers).json()["items"])  # noqa: E731
    assert titles_of(GUEST) == ["Summer", "Весна", "Зима"]
    r = client.post("/api/hidden", json={"kind": "artist", "key": "Кино"}, headers=GUEST)
    assert r.status_code == 200 and r.json()["on"]
    assert titles_of(GUEST) == ["Summer"]
    assert titles_of(GUEST, "?hidden=1") == ["Summer", "Весна", "Зима"]                # «показать скрытое»
    assert titles_of(AUTH) == ["Summer", "Весна", "Зима"]                              # the owner is not affected
    assert [a["name"] for a in client.get("/api/artists", headers=GUEST).json()["items"]] == ["Other"]
    assert [a["name"] for a in client.get("/api/albums", headers=GUEST).json()["items"]] == ["Hits"]
    wave = client.post("/api/wave/next", json={"moods": ["Весёлое"], "n": 10}, headers=GUEST).json()["items"]
    assert wave == []                                                                   # «Весна» is Кино: hidden
    owner_wave = client.post("/api/wave/next", json={"moods": ["Весёлое"]}, headers=AUTH).json()["items"]
    assert sorted(w["title"] for w in owner_wave) == ["Весна", "Зима"]               # «Зима»: the model's guess
    items = client.get("/api/hidden", headers=GUEST).json()["items"]
    assert [(i["kind"], i["key"]) for i in items] == [("artist", "Кино")]
    client.post("/api/hidden", json={"kind": "artist", "key": "Кино", "on": False}, headers=GUEST)       # «Вернуть»
    assert titles_of(GUEST) == ["Summer", "Весна", "Зима"]
    assert client.post("/api/hidden", json={"kind": "planet", "key": "x"}, headers=GUEST).status_code == 400


def test_deleted_track_leaves_the_queue_and_favourites(qtbot, app, library, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from musicplayer.core import trash
    from musicplayer.ui.pages.library import confirm_trash
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    tracks = ctx.db.search(limit=10)
    ctx.player.play_tracks(tracks, 0)
    victim = tracks[1]
    ctx.db.set_favorite("track", str(victim["id"]))
    monkeypatch.setattr(trash, "to_recycle_bin", lambda p: True)
    monkeypatch.setattr(QMessageBox, "exec", lambda box: (box.defaultButton().click(), 0)[1])
    assert confirm_trash(win, ctx, [victim])
    assert victim["id"] not in [t["id"] for t in ctx.player.queue.items]
    assert len(ctx.player.queue.items) == len(tracks) - 1
    fav = win.pages["favorites"]
    fav.refresh()
    assert victim["id"] not in [t["id"] for t in fav.tracks]


def test_hide_button_by_the_like_for_the_playing_track(qtbot, app, library, monkeypatch):
    from PySide6.QtWidgets import QMenu
    from musicplayer.core import hidden as hid
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    tracks = ctx.db.search(limit=10)
    ctx.player.play_tracks(tracks, 0)
    bar = win.player_bar
    assert bar.btn_hide.isEnabled() and not bar.btn_hide.icon().isNull()
    wave = win.pages["wave"]
    assert not wave.btn_hide.icon().isNull() and not wave.btn_other.icon().isNull()      # «скрыть» and the tag icon
    chosen = []

    def pick_first(menu, *a):                       # the first item: «трек «…»»
        items = [x for x in menu.actions() if not x.isSeparator()]
        chosen.append([x.text() for x in items])
        items[0].trigger()
    from musicplayer.ui import hideactions
    monkeypatch.setattr(hideactions, "show_menu", pick_first)
    first = ctx.player.current
    bar.btn_hide.click()
    assert chosen and chosen[0][0].startswith("трек") and any(x.startswith("исполнителя") for x in chosen[0])
    assert (str(first["id"]) in hid.keys(ctx.db, "owner")["track"]) and ctx.player.current["id"] != first["id"]
