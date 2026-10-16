"""The desktop app acts for a chosen listener (likes, «Не показывать»); the labels window for an album / artist."""

from tests.test_app_smoke import app, wait_job  # noqa: F401  (the app fixture)


def test_likes_and_hidden_for_the_chosen_listener(qtbot, app, library):
    from musicplayer.core import hidden, likes
    from musicplayer.ui.hideactions import hide
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    box = win.listener_box
    assert not box.isVisibleTo(win) and ctx.listener == "owner"            # nobody else: no chooser
    guest = win.sharing.add_user("Аня")
    qtbot.waitUntil(lambda: box.count() == 2, timeout=2000)
    assert box.isVisibleTo(win)
    box.setCurrentIndex(box.findData(guest["id"]))
    assert ctx.listener == guest["id"] and ctx.listener_name == "Аня"
    t = ctx.db.search(limit=1)[0]
    ctx.player.play_tracks([t])
    win.player_bar.toggle_favorite()                                       # the heart: Аня's like
    assert likes.keys(ctx.db, guest["id"], "track") == [str(t["id"])] and not ctx.db.favorites("track")
    assert "Аня" in win.player_bar.btn_fav.toolTip()
    fav = win.pages["favorites"]
    fav.refresh()
    assert [x["id"] for x in fav.tracks] == [t["id"]] and fav.table.item(0, 4).text()   # with the date it was liked
    hide(ctx, win.pages["library"], [("artist", t["artist"], "x")])
    assert hidden.counts(ctx.db, guest["id"]) == {"artist": 1} and not hidden.counts(ctx.db, "owner")
    assert t["id"] not in [r["id"] for r in win.pages["library"].model.rows]
    box.setCurrentIndex(box.findData("owner"))                             # back to me: my lists, my likes
    fav.refresh()
    assert ctx.listener == "owner" and fav.tracks == []
    assert t["id"] in [r["id"] for r in ctx.db.search(limit=100, hide_user=ctx.listener)]


def test_labels_window_for_the_whole_album(qtbot, app, library, monkeypatch):
    from musicplayer.ui import hideactions
    from musicplayer.ui.tagpopup import TagPopup, group_tracks
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    kino = [t for t in ctx.db.search(limit=100) if t["artist"] == "Кино"]
    ctx.player.play_tracks(kino, 0)
    album, by_artist = group_tracks(ctx.db, kino[0])
    assert len(album) == 2 and len(by_artist) == 2
    seen = []

    def pick(menu, _pos):                                                  # «альбома «…» (2)…»
        items = [a for a in menu.actions() if not a.isSeparator()]
        seen.append([a.text() for a in items])
        next(a for a in items if a.text().startswith("альбома")).trigger()
    monkeypatch.setattr(hideactions, "show_menu", pick)
    win.player_bar.btn_tags.click()
    assert seen and seen[0][0].startswith("трека") and any(x.startswith("исполнителя") for x in seen[0])
    pop = win.player_bar._tags
    assert isinstance(pop, TagPopup) and sorted(pop.tids) == sorted(t["id"] for t in kino) and "Альбом" in pop.title.text()
    pop.genres.buttons["Jazz"].click()                                     # one click: every track of the album
    assert all("Jazz" in ctx.db.get_track(t["id"])["genres"] for t in kino)
    assert not pop.btn_trash.isVisible()                                   # a whole album is not deleted from here


def test_favourites_sorted_by_date_first_then_any_column(qtbot, app, library):
    from PySide6.QtCore import Qt
    from musicplayer.core import likes
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    tracks = ctx.db.search(limit=10)
    for k, t in enumerate(tracks):                                        # liked one after another
        likes.set_liked(ctx.db, "owner", "track", str(t["id"]), True, added=1_700_000_000 + k * 3600)
    fav = win.pages["favorites"]
    fav.refresh()
    tab = fav.table
    shown = lambda: [tab.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in range(tab.rowCount())]   # noqa: E731
    assert shown() == [t["id"] for t in reversed(tracks)]                 # newest like first
    assert tab.horizontalHeader().sortIndicatorSection() == 4
    tab.sortByColumn(3, Qt.SortOrder.AscendingOrder)                      # by duration: seconds, not text
    durs = [t["duration"] for t in sorted(tracks, key=lambda t: t["duration"] or 0)]
    assert [next(x for x in tracks if x["id"] == i)["duration"] for i in shown()] == durs
    fav.refresh()                                                         # a refresh keeps the chosen column
    assert tab.horizontalHeader().sortIndicatorSection() == 3
    assert [x["id"] for x in fav.shown()] == shown()                      # play / double click follow the order


def test_hidden_genres_and_labels_leave_the_wave_chips_of_that_listener(qtbot, app):
    from musicplayer.ui.hideactions import hide
    ctx, win = app
    wave = win.pages["wave"]
    guest = win.sharing.add_user("Yoi")
    qtbot.waitUntil(lambda: win.listener_box.count() == 2, timeout=2000)
    win.listener_box.setCurrentIndex(win.listener_box.findData(guest["id"]))
    wave.genre_chips.set_selected({"Metal", "Djent"})
    mood = next(iter(wave.chips.buttons))
    wave.selected = {mood}
    hide(ctx, wave, [("genre", "Djent", "жанр"), ("genre", "Hip-Hop", "жанр"), ("mood", mood, "метку")])
    chips = wave.genre_chips
    assert not chips.buttons["Hip-Hop"].isVisibleTo(chips) and not chips.buttons["Djent"].isVisibleTo(chips)
    assert chips.buttons["Metal"].isVisibleTo(chips) and not wave.chips.buttons[mood].isVisibleTo(wave.chips)
    assert "Djent" not in chips.selected() and mood not in wave.selected       # a hidden choice is dropped
    win.listener_box.setCurrentIndex(win.listener_box.findData("owner"))     # my wave: everything is offered
    assert chips.buttons["Hip-Hop"].isVisibleTo(chips) and wave.chips.buttons[mood].isVisibleTo(wave.chips)


def test_reimport_with_dates_fixes_the_times_of_existing_likes(db):
    from musicplayer.core import likeimport, likes
    a = db.upsert_track({"path": "a.mp3", "title": "Numb", "artist": "Linkin Park"}, [])
    b = db.upsert_track({"path": "b.mp3", "title": "Believer", "artist": "Imagine Dragons"}, [])
    user = {"id": "u1", "owner": False}
    likeimport.apply(db, user, [a, b])                                      # first import: no dates in the file
    assert len({w for _, w in likes.rows(db, "u1", "track")}) == 2
    likeimport.apply(db, user, [a, b], [1_600_000_000.0, None])            # again, now with a date for one of them
    when = dict(likes.rows(db, "u1", "track"))
    assert when[str(a)] == 1_600_000_000.0 and when[str(b)] > 1_700_000_000   # undated keeps its time


def test_mini_player_like_labels_and_delete(qtbot, app, library, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from musicplayer.core import trash
    from musicplayer.ui import hideactions
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    tracks = ctx.db.search(limit=10)
    ctx.player.play_tracks(tracks, 0)
    mini = win.mini
    t = ctx.player.current
    mini.btn_like.click()                                                   # ♥ from the mini player
    assert ctx.is_liked("track", str(t["id"])) and "Убрать" in mini.btn_like.toolTip()
    assert win.player_bar.btn_fav.toolTip().startswith("Убрать")           # the player bar follows
    seen = []
    monkeypatch.setattr(hideactions, "show_menu", lambda menu, pos: seen.append([a.text() for a in menu.actions()]))
    mini.btn_tags.click()                                                   # labels: track / album / artist
    assert seen and any(x.startswith("трека") for x in seen[0])
    monkeypatch.setattr(trash, "to_recycle_bin", lambda p: True)
    monkeypatch.setattr(QMessageBox, "exec", lambda box: (box.defaultButton().click(), 0)[1])
    mini.btn_trash.click()                                                  # delete (asks first)
    assert t["id"] not in [x["id"] for x in ctx.db.search(limit=100)]
