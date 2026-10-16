import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def app(qtbot, tmp_path, library):
    from musicplayer.ui.app import create_app
    _, ctx, win = create_app(home=tmp_path / "home", mcp=False)
    qtbot.addWidget(win)
    ctx.settings.library_root = str(library)
    win.show()
    win.start_scan(str(library))
    qtbot.waitUntil(lambda: not ctx.jobs.busy, timeout=60000)
    yield ctx, win
    ctx.jobs.cancel()
    qtbot.waitUntil(lambda: not ctx.jobs.busy, timeout=30000)


def test_seek_bar_click_jumps_and_drag_seeks(qtbot, app):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    ctx, win = app
    track = ctx.db.search(limit=1)[0]
    track["duration"] = 200.0
    ctx.player.queue.set([track])
    seen = []
    ctx.player.seek = lambda s: seen.append(round(s, 1))           # record instead of seeking a silent test file
    ctx.player.duration = lambda: 200.0
    bar = win.player_bar.seek
    bar.resize(1000, 20)
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=QPoint(250, 10))   # a click on the groove, no drag
    assert seen and abs(seen[-1] - 50.0) < 1.5 and abs(bar.value() - 250) <= 2
    QTest.mousePress(bar, Qt.MouseButton.LeftButton, pos=QPoint(100, 10))
    QTest.mouseMove(bar, QPoint(900, 10))
    QTest.mouseRelease(bar, Qt.MouseButton.LeftButton, pos=QPoint(900, 10))
    assert abs(seen[-1] - 180.0) < 1.5
    bar.setFocus()
    QTest.keyClick(bar, Qt.Key.Key_Right)
    assert seen[-1] > 180.0
    win.player_bar.on_position(10.0, 200.0)                         # playback moves the handle when not dragging
    assert abs(bar.value() - 50) <= 1


def test_real_player_seek_fraction_uses_the_track_length(qtbot, app):
    ctx, win = app
    track = ctx.db.search(limit=1)[0]
    track["duration"] = 120.0
    ctx.player.queue.set([track])
    seen = []
    ctx.player.seek = lambda s: seen.append(s)
    assert ctx.player.duration() in (120.0,) or ctx.player.duration() > 0   # engine value or the tag length
    ctx.player.__class__.seek_fraction(ctx.player, 0.25)
    assert seen and abs(seen[-1] - 0.25 * ctx.player.duration()) < 1e-6


def test_rating_buttons_are_disabled_after_a_click_until_the_next_track(qtbot, app):
    ctx, win = app
    ids = [t["id"] for t in ctx.db.search(limit=3)]
    ctx.db.suggest_moods([(ids[0], "Грустное", 0.9), (ids[1], "Грустное", 0.9)], "model")
    wave = win.pages["wave"]
    wave.select_moods(["Грустное"])
    assert wave.start() == 2
    assert wave.btn_like.isEnabled() and wave.btn_dislike.isEnabled()
    first = ctx.player.current["id"]
    wave.rate(True)
    assert not wave.btn_like.isEnabled() and not wave.btn_dislike.isEnabled() and wave.btn_like.toolTip() == "Уже оценено"
    n_before = len(ctx.db.mood_negatives())
    wave.rate(False)                                                # a second click on the same track does nothing
    assert len(ctx.db.mood_negatives()) == n_before and ctx.player.current["id"] == first
    ctx.player.next()                                               # the next track can be rated again
    assert ctx.player.current["id"] != first and wave.btn_like.isEnabled() and wave.btn_dislike.isEnabled()


def test_mood_vocabulary_is_grouped_ordered_and_migrated(qtbot, app, tmp_path):
    from musicplayer.core import moods
    from musicplayer.core.db import Database
    ctx, win = app
    names = [m["name"] for m in ctx.db.list_moods()]
    assert names[:10] == moods.DEFAULT_MOODS[:10] and len(names) >= 26 and "Бунтарское" in names and "Эйфорическое" not in names
    ctx.db.add_mood("Моё особенное")                               # user-defined goes to the end, its own section
    assert [m["name"] for m in ctx.db.list_moods()][-1] == "Моё особенное"
    lib = win.pages["library"]
    lib.reload_lists()
    assert "Бунтарское" in lib.chips.buttons and not lib.chips.needs_update([m["name"] for m in ctx.db.list_moods()])
    assert moods.group_of("Тяжёлое") == moods.ROCK and moods.group_of("Угарное") == moods.RAVE and moods.group_of("x") == moods.CUSTOM
    # an old catalogue (10 moods, user_version 2) receives the new vocabulary once
    old = Database(tmp_path / "old.db")
    old.conn.execute("DELETE FROM moods WHERE name NOT IN ('Весёлое','Грустное')")
    old.conn.execute("PRAGMA user_version=2")
    old.close()
    again = Database(tmp_path / "old.db")
    assert len(again.list_moods()) == len(moods.DEFAULT_MOODS)
    assert all(m["prompt"] for m in again.list_moods())


def test_title_shows_version_icon_exists_and_new_moods_present(qtbot, app):
    from musicplayer import __version__
    from musicplayer.core.paths import resource_dir
    ctx, win = app
    assert win.windowTitle() == f"Walle Player {__version__}" and __version__.count(".") == 2
    assert (resource_dir() / "icon.ico").exists() and not win.windowIcon().isNull()
    names = [m["name"] for m in ctx.db.list_moods()]
    assert "Рейв" in names and "Ритмичное" in names and "Угарное" in names          # rave and rhythmic are separate moods
    from musicplayer.core import moods
    assert moods.group_of("Рейв") == moods.RAVE and moods.group_of("Ритмичное") == moods.BASIC
    prompts = {m["name"]: m["prompt"] for m in ctx.db.list_moods()}
    assert "rave" in prompts["Рейв"] and "rhythmic" in prompts["Ритмичное"]


def test_wave_wrong_guess_lets_the_user_choose_other_moods(qtbot, app):
    ctx, win = app
    ids = [t["id"] for t in ctx.db.search(limit=3)]
    ctx.db.suggest_moods([(ids[0], "Грустное", 0.9), (ids[1], "Грустное", 0.9)], "model")
    wave = win.pages["wave"]
    wave.select_moods(["Грустное"])
    assert wave.start() == 2
    cur = ctx.player.current
    wave.rate(False)                                              # thumbs down opens the chooser, nothing is lost yet
    assert wave.fix_box.isVisibleTo(wave) and not ctx.db.mood_negatives()
    wave._fix_toggled("Ритмичное", True)
    wave._fix_toggled("Рейв", True)
    wave.save_fix()
    t = ctx.db.get_track(cur["id"])
    assert set(t["moods"]) == {"Ритмичное", "Рейв"} and set(t["mood_sources"].values()) == {"manual"}
    assert ctx.db.mood_negatives() == {cur["id"]: {"Грустное"}}   # the wrong guess is remembered as rejected
    assert not wave.fix_box.isVisibleTo(wave) and not wave.btn_like.isEnabled() and wave.btn_other.isEnabled()
    assert ctx.player.current["id"] == cur["id"]                  # saving does not skip the track
    ctx.player.next()                                             # the next track: the chooser starts from its labels
    nxt = ctx.player.current
    wave.open_fix()
    assert wave.fix_box.isVisibleTo(wave) and wave.fix_selected == set(ctx.db.get_track(nxt["id"])["moods"]) - {"Грустное"}
    wave.save_fix()                                               # nothing chosen -> asks to choose, saves nothing
    assert "Отметьте" in wave.toast.text() and not ctx.db.get_track(nxt["id"])["moods"]
    wave.reject_and_skip()                                        # "no mood fits": reject the guess and move on
    assert ctx.db.mood_negatives().get(nxt["id"]) == {"Грустное"}


def test_mcp_style_correction_sets_manual_labels(qtbot, app):
    ctx, win = app
    t = ctx.db.search(limit=1)[0]
    ctx.db.suggest_moods([(t["id"], "Грустное", 0.9)], "model")
    wave = win.pages["wave"]
    wave.select_moods(["Грустное"])
    wave.start()
    wave.open_fix()
    wave.fix_selected = {"Спокойное"}
    wave.save_fix()
    assert ctx.db.get_track(t["id"])["moods"] == ["Спокойное"]


def test_window_can_be_narrow_and_mini_player_is_tiny_and_works(qtbot, app):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    ctx, win = app
    assert win.player_bar.minimumSizeHint().width() <= 400          # was 975: secondary controls hide when narrow
    win.resize(1400, 520)
    qtbot.wait(50)
    assert win.player_bar.btn_shuffle.isVisibleTo(win.player_bar) and win.player_bar.vol.isVisibleTo(win.player_bar)
    win.resize(500, 520)                                             # the window itself may be narrow (it was >2800 px)
    qtbot.wait(50)
    assert win.width() <= 520 and win.minimumWidth() <= 420
    assert not win.player_bar.btn_shuffle.isVisibleTo(win.player_bar) and not win.player_bar.vol.isVisibleTo(win.player_bar)
    assert win.player_bar.btn_play.isVisibleTo(win.player_bar) and win.player_bar.btn_mini.isVisibleTo(win.player_bar)
    mini = win.mini
    assert mini.width() <= 440 and mini.height() <= 110
    track = ctx.db.search(limit=1)[0]
    track["duration"] = 100.0
    ctx.player.queue.set([track])
    played = []
    ctx.player.toggle = lambda: played.append("toggle")
    ctx.player.next = lambda: played.append("next")
    seen = []
    ctx.player.seek_fraction = lambda f: seen.append(round(f, 2))
    win.enter_mini()
    assert mini.isVisible() and not win.isVisible()
    mini.btn_play.click(); mini.btn_next.click()
    assert played == ["toggle", "next"]
    mini.on_track(track)
    assert track["title"][:5] in mini.title.text() or "…" in mini.title.text()
    mini.seek.resize(300, 14)
    QTest.mouseClick(mini.seek, Qt.MouseButton.LeftButton, pos=QPoint(150, 7))
    assert seen and abs(seen[-1] - 0.5) < 0.02
    mini.on_position(25.0, 100.0)
    assert abs(mini.seek.value() - 250) <= 1
    mini.btn_pin.setChecked(False)                                   # unpinning keeps the window visible
    assert mini.isVisible()
    mini.btn_expand.click()
    assert win.isVisible() and not mini.isVisible() and ctx.settings.mini_pos is not None


def test_wave_edit_button_opens_full_labels(qtbot, app, library):
    from tests.test_app_smoke import wait_job
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    wave = win.pages["wave"]
    ctx.player.play_tracks(ctx.db.search(limit=2))
    wave.open_tags()
    pop = wave._tags
    assert pop.isVisible() and pop.flags.buttons and pop.genres.buttons and pop.lv.lang_btns and pop.era.buttons
    pop.hide()


def test_each_window_mode_keeps_its_own_place(qtbot, app):
    """The full window and the mini player each remember their own position (the mini one right after a drag)."""
    from PySide6.QtCore import QPoint
    ctx, win = app
    win.show()
    win.setGeometry(20, 30, 700, 500)
    qtbot.wait(30)
    assert not hasattr(win.player_bar, "btn_compact") and not hasattr(win, "toggle_compact")   # no middle-size view
    full_pos = win.pos()
    win.enter_mini()
    win.mini.move(QPoint(40, 300))
    win.mini.moved.emit(win.mini.pos())                                  # dragged: remembered at once
    assert ctx.settings.mini_pos == [40, 300]
    win.leave_mini()
    assert win.pos() == full_pos
    win.enter_mini()
    assert win.mini.pos() == QPoint(40, 300)
    for b in (win.mini.btn_prev, win.mini.btn_play, win.mini.btn_next, win.mini.btn_expand, win.mini.btn_pin):
        assert b.toolTip() and not b.icon().isNull()                     # icons, not clipped text glyphs; tooltips
    win.leave_mini()


def test_mini_player_volume_button_opens_a_vertical_slider(qtbot, app):
    from PySide6.QtCore import Qt
    ctx, win = app
    ctx.player.set_volume(0.5)
    win.enter_mini()
    mini = win.mini
    assert "50%" in mini.btn_vol.toolTip()
    mini.btn_vol.click()
    pop = mini.vol_popup
    assert pop.isVisible() and pop.slider.orientation() == Qt.Orientation.Vertical and pop.slider.value() == 50
    pop.slider.setValue(30)
    assert abs(ctx.player.volume - 0.3) < 1e-6 and "30%" in mini.btn_vol.toolTip() and pop.label.text() == "30%"
    pop.step(5)
    assert abs(ctx.player.volume - 0.35) < 1e-6
    pop.hide()
    win.leave_mini()
    assert win.player_bar.vol.value() == 35                                 # the full window's slider follows
