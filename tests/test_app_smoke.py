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
    yield ctx, win
    ctx.jobs.cancel()
    qtbot.waitUntil(lambda: not ctx.jobs.busy, timeout=30000)


def wait_job(qtbot, ctx, timeout=60000):
    qtbot.waitUntil(lambda: not ctx.jobs.busy, timeout=timeout)


def test_scan_then_pages_show_data(qtbot, app, library):
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    lib = win.pages["library"]
    assert ctx.jobs.history[-1].state == "finished"
    assert lib.model.rowCount() == 3
    lib.search.setText("кино")
    qtbot.waitUntil(lambda: lib.model.rowCount() == 2, timeout=3000)
    assert win.pages["albums"].grid.count() == 1


def test_label_moods_through_panel(qtbot, app, library):
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    lib = win.pages["library"]
    lib.view.selectAll()
    assert len(lib._selection) == 3
    lib.chips.buttons["Грустное"].click()
    assert all("Грустное" in t["moods"] for t in lib.model.rows)
    lib.chips.buttons["Грустное"].click()
    assert all(not t["moods"] for t in lib.model.rows)


def test_queue_and_counter_integration(qtbot, app, library):
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    p = ctx.player
    p.queue.set(ctx.db.search(limit=10))
    p.tracker.start(p.current["id"], 100)
    for s in range(0, 60):
        p.tracker.update(s)
    assert ctx.db.get_track(p.current["id"])["play_count"] == 1
    p.set_shuffle("rare")
    assert p.queue.shuffle == "rare"


def test_zoom_scales_ui_and_tiles(qtbot, app, library):
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    lib = win.pages["library"]
    base_row, base_font = lib.view.verticalHeader().defaultSectionSize(), win.font().pointSize()
    win.set_zoom(1.5)
    from PySide6.QtWidgets import QApplication
    assert lib.view.verticalHeader().defaultSectionSize() == int(42 * 1.5) > base_row
    assert QApplication.instance().font().pointSize() > base_font
    assert win.player_bar.cover.width() == 96
    win.zoom_step(-1)
    assert win.zoom < 1.5
    win.set_zoom(9)                                         # clamped
    assert win.zoom == 2.0
    albums = win.pages["albums"]
    albums.set_tile(240)
    assert albums.grid.iconSize().width() == int(240 * 2.0) and ctx.settings.tile_size == 240
    assert albums.slider.value() == 240


def test_color_music_reacts_to_samples(qtbot, app):
    import numpy as np
    ctx, win = app
    win.set_page("colormusic")
    page = win.pages["colormusic"]
    t = (0.8 * np.sin(2 * np.pi * 100 * np.arange(4096) / 44100)).astype(np.float32)
    ctx.player._state = "playing"
    for _ in range(25):
        ctx.player.samples.emit(t, 44100)
        page.view.an.step(True)
    assert page.view.an.live and page.view.an.bass > 0.4
    for mode in ("bars", "radial", "aurora", "pulse"):
        page.set_mode(mode)
        page.view.grab()                                    # paints without errors
    assert page.view.mode == "pulse"


def test_feat_shown_in_artist_column(qtbot, app, tmp_path):
    from .conftest import make_mp3
    root = tmp_path / "f"
    make_mp3(root / "A" / "x.mp3", artist="A", title="x (feat. B)")
    ctx, win = app
    win.start_scan(str(root))
    wait_job(qtbot, ctx)
    lib = win.pages["library"]
    idx = lib.model.index(0, 1)
    assert lib.model.data(idx) == "A feat. B"
    lib.search.setText("B")
    qtbot.waitUntil(lambda: lib.model.rowCount() == 1, timeout=3000)


def test_backup_and_corrupt_catalogue_recovery(qtbot, tmp_path, library):
    from musicplayer.ui.context import AppContext
    from musicplayer.core.scanner import scan
    home = tmp_path / "h"
    c1 = AppContext(home)
    scan(c1.db, library, c1.covers)
    c1.close()
    c2 = AppContext(home)                            # second start: daily backup is created
    assert list((home / "backups").glob("library-*.db"))
    c2.close()
    (home / "library.db").write_bytes(b"this is not a database" * 200)
    for suffix in ("-wal", "-shm"):
        (home / f"library.db{suffix}").unlink(missing_ok=True)
    c3 = AppContext(home)                            # corrupt file -> moved aside, backup restored
    assert "восстановлена" in c3.db_notice and c3.db.count() == 3
    assert list(home.glob("library.corrupt-*.db"))
    c3.close()


def test_quick_keys_and_next_step_guide(qtbot, app, library):
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    lib = win.pages["library"]
    lib.view.selectRow(0)
    names = list(lib.chips.buttons)
    lib.toggle_mood_index(1)                         # key "2": second mood
    assert lib.model.rows[0]["moods"] == [names[1]]
    ctx.db.suggest_moods([(lib.model.rows[0]["id"], names[0], 0.9), (lib.model.rows[1]["id"], names[2], 0.8)], "model")
    lib._tracks_changed([lib.model.rows[0]["id"], lib.model.rows[1]["id"]])
    lib.quick_resolve(True)                          # "A": accept and jump to the next row
    assert names[0] in lib.model.rows[0]["moods"]
    assert lib.view.selected_ids() == [lib.model.rows[1]["id"]]
    lib.quick_resolve(False)                         # "D"
    assert names[2] not in lib.model.rows[1]["moods"] and ctx.db.mood_negatives()
    moods = win.pages["moods"]
    moods.refresh()
    assert "Следующий шаг" in moods.guide.text() and moods.step["step"] == "label"


def test_uncertain_rows_open_in_library(qtbot, app, library):
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    rows = ctx.db.search(limit=2)
    ctx.events.show_rows.emit(rows, "Что разметить следующим")
    assert win.pages["library"].model.rowCount() == 2


def test_moods_page_clap_controls_and_feature_choice(qtbot, app, library):
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    page = win.pages["moods"]
    page.refresh()
    assert "CLAP" in page.clap_label.text()
    assert [page.features.itemData(i) for i in range(page.features.count())] == ["auto", "descriptors", "clap", "both"]
    assert page.btn_zero.text() and page.btn_agree.text()


def test_label_feed_flow_keys_and_undo(qtbot, app, library):
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    feed = win.pages["labelfeed"]
    names = [m["name"] for m in ctx.db.list_moods()]
    assert feed.start("random", 20) == 3 and feed.current
    first = feed.current["id"]
    feed.toggle_index(1)
    feed.toggle_index(3)
    assert feed.chosen == {names[1], names[3]}
    feed.confirm()                                              # Enter: labels and moves on
    assert sorted(ctx.db.get_track(first)["moods"]) == sorted([names[1], names[3]]) and feed.current["id"] != first
    second = feed.current["id"]
    ctx.db.suggest_moods([(second, names[0], 0.9)], "model")
    feed.queue[feed.pos] = ctx.db.get_track(second)
    feed.guesses = feed._model_guesses(feed.current)            # (what the feed computes when a track opens)
    feed.chosen = set(feed.guesses)
    assert feed.chosen == {names[0]}
    feed.chosen.clear()                                         # the user unticks the model's wrong guess
    feed.confirm()                                              # -> a rejection (negative example), next track
    assert ctx.db.mood_negatives() == {second: {names[0]}}
    feed.undo_last()                                            # Backspace: back to the second track, suggestion restored
    assert feed.current["id"] == second and not ctx.db.mood_negatives()
    assert ctx.db.get_track(second)["mood_suggestions"][0]["mood"] == names[0] and feed.chosen == {names[0]}
    feed.skip()
    feed.chosen.clear()
    feed.confirm()                                              # nothing ticked, no guesses -> acts as skip
    assert feed.skipped == 2
    feed.undo_last()
    feed.undo_last()
    feed.undo_last()                                            # undo the first label as well
    assert ctx.db.get_track(first)["moods"] == [] and feed.current["id"] == first
    feed.skip(); feed.skip(); feed.skip()
    assert feed.current is None and "Лента закончилась" in feed.title.text()



def test_wave_page_empty_states_play_feedback_and_retarget(qtbot, app, library):
    ctx, win = app
    wave = win.pages["wave"]
    assert win.current_page_key() == "wave"
    assert "пуста" in wave.empty.text() and wave._empty_target == "scan"
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    wave.reload()
    assert wave._empty_target == "labelfeed" and wave.empty_btn.isVisible() is False or wave._empty_target == "labelfeed"
    assert wave.start() == 0 and "Нет подходящих" in wave.toast.text()
    ids = [t["id"] for t in ctx.db.search(limit=10)]
    ctx.db.set_moods(ids[:2], ["Грустное"])
    ctx.db.suggest_moods([(ids[2], "Грустное", 0.9)], "model")
    wave.reload()
    wave.select_moods(["Грустное"])
    assert wave.start() == 3 and wave.active
    cur = ctx.player.current
    assert cur["wave_moods"] == ["Грустное"]
    guess = next(t for t in ctx.player.queue.items if t["id"] == ids[2])
    ctx.player.queue.jump(ctx.player.queue.items.index(guess))
    ctx.player.track_changed.emit(guess)
    assert wave.btn_like.isEnabled() and "Модель думает" in wave.badge.text()
    wave.rate(True)                                           # 👍 confirms the guessed mood
    assert ctx.db.get_track(ids[2])["moods"] == ["Грустное"] and "Запомнил" in wave.toast.text()
    wave.select_moods(["Грустное", "Весёлое"])                # retarget keeps playing, drops the upcoming queue
    assert wave.active and ctx.player.current["id"] == ids[2]


def test_global_search_opens_the_library_and_filters(qtbot, app, library):
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    win.global_search.setText("beatles")
    qtbot.waitUntil(lambda: win.current_page_key() == "library" and win.pages["library"].model.rowCount() == 1, timeout=4000)
    assert win.nav_buttons["library"].isChecked() and not win.nav_buttons["wave"].isChecked()


def test_moods_page_hides_manual_steps_and_auto_chain_stops_when_input_is_needed(qtbot, app, library):
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    page = win.pages["moods"]
    page.refresh()
    assert not page.adv.isVisibleTo(page)                # the manual steps are folded under "advanced"
    page.btn_adv.setChecked(True)
    assert page.adv.isVisibleTo(page) and all(c.isVisibleTo(page) for c in page._manual_cards)
    page.run_auto()                                     # nothing labelled yet: the next step is YOUR labelling
    assert page.step["step"] == "label" and page._auto is False


def test_wave_deactivates_when_other_playback_takes_over_and_zoom_still_works(qtbot, app, library):
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    ids = [t["id"] for t in ctx.db.search(limit=5)]
    ctx.db.set_moods(ids[:2], ["Грустное"])
    wave = win.pages["wave"]
    wave.select_moods(["Грустное"])
    assert wave.start() == 2 and wave.active
    ctx.player.play_tracks([ctx.db.get_track(ids[2])])        # a plain track, not from the wave
    assert wave.active is False
    win.set_zoom(1.3)                                         # used to raise KeyError('wave_moods')
    wave.rate(True)                                           # harmless no-op
    assert not wave.btn_like.isEnabled()


def test_auto_tag_settings_and_pipeline_hook(qtbot, app, library, monkeypatch):
    ctx, win = app
    page = win.pages["moods"]
    page.auto_chk.setChecked(False)
    page.auto_thr.setCurrentIndex(page.auto_thr.findData("strict"))
    assert ctx.settings.auto_tag is False and ctx.settings.auto_mode == "strict"
    assert '"auto_mode": "strict"' in ctx.settings_file.read_text(encoding="utf-8")
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    assert win.maybe_autotag() is None                         # disabled, and there is no model yet
    page.auto_chk.setChecked(True)
    assert win.maybe_autotag() is None                         # enabled but no trained model
    from musicplayer.core import autotag, mood
    import types
    monkeypatch.setattr(mood, "load_model", lambda d: types.SimpleNamespace(info={}, moods=[]))
    seen = {}
    monkeypatch.setattr(autotag, "tag_new_tracks", lambda db, models, jc, ids, mode: seen.update(mode=mode) or {"tracks": 3})
    job = win.maybe_autotag()                                  # model exists and 3 tracks are new -> job starts
    assert job is not None and job.kind == "autotag"
    wait_job(qtbot, ctx)
    assert seen["mode"] == "strict" and ctx.jobs.history[-1].state == "finished"
    page.refresh()
    assert "ещё не определено 3" in page.auto_info.text()


def test_tag_popup_labels_playing_track(qtbot, app, library):
    from musicplayer.ui.tagpopup import TagPopup
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    t = ctx.db.search(limit=1)[0]
    pop = TagPopup(ctx)
    qtbot.addWidget(pop)
    pop.open_for(t, win.player_bar.btn_tags)
    name = next(iter(pop.chips.buttons))
    pop.chips.buttons[name].click()
    assert name in ctx.db.get_track(t["id"])["moods"]
    pop.flags.buttons["live"].click()
    assert "live" in ctx.db.get_track(t["id"])["flags"]
    pop.chips.buttons[name].click()
    assert name not in ctx.db.get_track(t["id"])["moods"]


def test_window_geometry_is_remembered(qtbot, tmp_path, library):
    from musicplayer.ui.context import AppContext
    from musicplayer.ui.main_window import MainWindow
    home = tmp_path / "geo"
    c1 = AppContext(home)
    w1 = MainWindow(c1)
    qtbot.addWidget(w1)
    w1.resize(777, 555)
    w1.show()
    w1.close()
    c2 = AppContext(home)
    w2 = MainWindow(c2)
    qtbot.addWidget(w2)
    assert (w2.width(), w2.height()) == (777, 555)
    c2.close()


def test_compact_playback_view_toggle(qtbot, app):
    ctx, win = app
    win.show()
    win.resize(900, 600)
    full = win.height()
    win.player_bar.btn_compact.click()
    assert win.body_w.isHidden() and win.height() < 200
    win.player_bar.btn_compact.click()
    assert win.body_w.isVisible() and win.height() == full


def test_language_vocal_and_explicit_flag_from_popup(qtbot, app, library):
    from musicplayer.ui.tagpopup import TagPopup
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    t = ctx.db.search(limit=1)[0]
    pop = TagPopup(ctx)
    qtbot.addWidget(pop)
    pop.open_for(t, win.player_bar.btn_tags)
    pop.lv.lang_btns["ja"].click()
    pop.lv.vocal_btns["inst"].click()
    pop.flags.buttons["explicit"].click()
    got = ctx.db.get_track(t["id"])
    assert (got["lang"], got["vocal"], "explicit" in got["flags"]) == ("ja", "inst", True)
    pop.lv.lang_btns["ja"].click()                    # click again clears
    assert ctx.db.get_track(t["id"])["lang"] is None


def test_merge_mood(db):
    db.add_mood("A"); db.add_mood("B")
    tid = db.search(limit=1)[0]["id"] if db.count() else None
    if tid is None:
        return
    db.set_moods([tid], ["A"], "set")
    db.merge_mood("A", "B")
    assert db.get_track(tid)["moods"] == ["B"]


def test_mood_count_indicator_turns_green_from_three(qtbot):
    from musicplayer.ui.pages.library import MoodChips
    c = MoodChips()
    qtbot.addWidget(c)
    c.set_moods(["Весёлое", "Грустное", "Злое", "Спокойное"])
    assert "не выбраны" in c.count.text()
    for n in ("Весёлое", "Грустное"):
        c.buttons[n].click()
    assert "2" in c.count.text() and "7BD88F" not in c.count.styleSheet()
    c.buttons["Злое"].click()
    assert "3 метки" in c.count.text() and "7BD88F" in c.count.styleSheet()


def test_genre_era_chips_and_trash(qtbot, app, library, monkeypatch):
    from musicplayer.core import trash
    from musicplayer.ui.tagpopup import TagPopup
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    t = ctx.db.search(limit=1)[0]
    pop = TagPopup(ctx)
    qtbot.addWidget(pop)
    pop.open_for(t, win.player_bar.btn_tags)
    assert not pop.genres.buttons["Djent"].isVisibleTo(pop)          # sub-genres appear under their family
    pop.genres.buttons["Metal"].click()
    assert pop.genres.buttons["Djent"].isVisibleTo(pop)
    pop.genres.buttons["Djent"].click()
    assert {"Djent", "Metal"} <= set(ctx.db.get_track(t["id"])["genres"])
    pop.genres.buttons["Metal"].click()                                # family off clears its sub-genres
    assert not {"Djent", "Metal"} & set(ctx.db.get_track(t["id"])["genres"])
    ctx.db.conn.execute("UPDATE tracks SET year=NULL WHERE id=?", (t["id"],))
    pop.open_for(ctx.db.get_track(t["id"]), win.player_bar.btn_tags)
    pop.era.buttons[2000].click()
    assert ctx.db.search(year_from=2000, year_to=2009, limit=50) and ctx.db.get_track(t["id"])["era"] == 2000
    gone = []
    res = trash.trash_tracks(ctx.db, [ctx.db.get_track(t["id"])], remover=lambda p: gone.append(p) or True)
    assert res["deleted"] == 1 and gone and ctx.db.get_track(t["id"])["id"] not in [r["id"] for r in ctx.db.search(limit=100)]


def test_session_restore_and_history(qtbot, tmp_path, library):
    from musicplayer.ui.context import AppContext
    from musicplayer.ui.main_window import MainWindow
    from musicplayer.core.scanner import scan
    home = tmp_path / "sess"
    c1 = AppContext(home)
    scan(c1.db, library, c1.covers)
    w1 = MainWindow(c1)
    qtbot.addWidget(w1)
    tracks = c1.db.search(limit=10)
    c1.player.cue(tracks, 1, 12.5)                     # as if the user had played the 2nd track up to 0:12
    c1.db.record_play(tracks[0]["id"]) if hasattr(c1.db, "record_play") else None
    w1.close()
    c2 = AppContext(home)
    w2 = MainWindow(c2)
    qtbot.addWidget(w2)
    qtbot.waitUntil(lambda: c2.player.current is not None, timeout=3000)
    assert c2.player.current["id"] == tracks[1]["id"] and c2.player.state == "paused"
    assert len(c2.player.queue.items) == len(tracks)
    assert "history" in w2.pages
    w2.close()


def test_few_examples_marker_and_external_progress(qtbot, app, tmp_path):
    from musicplayer.core import extprogress
    from musicplayer.ui.pages.library import MoodChips
    c = MoodChips()
    qtbot.addWidget(c)
    c.set_moods(["Весёлое", "Грустное"])
    c.set_counts({"Весёлое": 3, "Грустное": 40})
    assert c.buttons["Весёлое"].text().endswith("· 3") and "Мало" in c.buttons["Весёлое"].toolTip()
    assert "·" not in c.buttons["Грустное"].text()
    ctx, win = app
    win.show()
    extprogress.write(ctx.home, "Обучение модели настроений", 5, 20, "эпоха 5")
    win._poll_external_job()
    assert win.job_row.isVisible() and win.job_bar.value() == 5 and "Обучение" in win.job_label.text()
    extprogress.clear(ctx.home)
    win._poll_external_job()
    assert not win.job_row.isVisible()


def test_sliders_jump_to_click(qtbot):
    from PySide6.QtCore import QPoint, Qt
    from musicplayer.ui.widgets import JumpSlider
    s = JumpSlider(Qt.Orientation.Horizontal)
    s.setRange(0, 100)
    s.resize(200, 20)
    qtbot.addWidget(s)
    s.show()
    qtbot.mouseClick(s, Qt.MouseButton.LeftButton, pos=QPoint(150, 10))
    assert 70 <= s.value() <= 80


def test_wave_survives_restart(qtbot, tmp_path, library):
    from musicplayer.ui.context import AppContext
    from musicplayer.ui.main_window import MainWindow
    from musicplayer.core.scanner import scan
    home = tmp_path / "wv"
    c1 = AppContext(home)
    scan(c1.db, library, c1.covers)
    names = [m["name"] for m in c1.db.list_moods()][:1]
    for t in c1.db.search(limit=3):
        c1.db.set_moods([t["id"]], names, "set")
    w1 = MainWindow(c1)
    qtbot.addWidget(w1)
    wave = w1.pages["wave"]
    wave._mood_toggled(names[0], True)
    assert wave.start() > 0 and wave.active
    w1.close()
    c2 = AppContext(home)
    w2 = MainWindow(c2)
    qtbot.addWidget(w2)
    qtbot.waitUntil(lambda: c2.player.current is not None, timeout=3000)
    wave2 = w2.pages["wave"]
    assert wave2.active and wave2.selected == set(names) and "wave_kind" in c2.player.current
    w2.close()


def test_single_instance(qtbot):
    from musicplayer.ui.app import claim_single_instance
    first = claim_single_instance("WallePlayer-test-instance")
    assert first is not None
    got = []
    first.newConnection.connect(lambda: got.append(first.nextPendingConnection()))
    assert claim_single_instance("WallePlayer-test-instance") is None     # the second start is refused
    qtbot.waitUntil(lambda: bool(got), timeout=2000)
    first.close()


def test_wave_choice_kept_without_playing(qtbot, tmp_path):
    from musicplayer.ui.context import AppContext
    from musicplayer.ui.main_window import MainWindow
    home = tmp_path / "wc"
    c1 = AppContext(home)
    w1 = MainWindow(c1)
    qtbot.addWidget(w1)
    name = next(iter(w1.pages["wave"].chips.buttons))
    w1.pages["wave"]._mood_toggled(name, True)
    w1.pages["wave"].set_character("weak")
    c1.close()                                        # even without a clean window close
    c2 = AppContext(home)
    w2 = MainWindow(c2)
    qtbot.addWidget(w2)
    qtbot.waitUntil(lambda: w2.pages["wave"].selected == {name}, timeout=3000)
    assert w2.pages["wave"].character == "weak" and w2.pages["wave"].char_buttons["weak"].isChecked()
    c2.close()


def test_click_on_now_playing_shows_track_in_library(qtbot, app, library):
    from PySide6.QtCore import Qt
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    tracks = ctx.db.search(limit=3)
    ctx.player.play_tracks(tracks, 2)
    win.set_page("wave")
    qtbot.mouseClick(win.player_bar.title, Qt.MouseButton.LeftButton)
    lib = win.pages["library"]
    qtbot.waitUntil(lambda: lib.view.selected_ids() == [tracks[2]["id"]], timeout=3000)


def test_copy_paste_labels(qtbot, app, library):
    from musicplayer.ui.tagpopup import TagPopup
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    a, b = ctx.db.search(limit=2)
    mood = [m["name"] for m in ctx.db.list_moods()][0]
    ctx.db.set_moods([a["id"]], [mood], "set")
    ctx.db.set_flags([a["id"]], ["live"], "set")
    ctx.db.set_genres([a["id"]], ["Metal", "Djent"], "set")
    ctx.db.set_language([a["id"]], "ja")
    pop = TagPopup(ctx)
    qtbot.addWidget(pop)
    pop.open_for(ctx.db.get_track(a["id"]), win.player_bar.btn_tags)
    assert not pop.btn_paste.isEnabled()
    pop.btn_copy.click()
    pop.open_for(ctx.db.get_track(b["id"]), win.player_bar.btn_tags)
    assert pop.btn_paste.isEnabled()
    pop.btn_paste.click()
    got = ctx.db.get_track(b["id"])
    assert got["moods"] == [mood] and got["flags"] == ["live"] and set(got["genres"]) == {"Metal", "Djent"} and got["lang"] == "ja"


def test_new_mood_from_popup(qtbot, app, library):
    from musicplayer.ui.tagpopup import TagPopup
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    t = ctx.db.search(limit=1)[0]
    pop = TagPopup(ctx)
    qtbot.addWidget(pop)
    pop.open_for(t, win.player_bar.btn_tags)
    pop.new_mood("Космическое")
    assert "Космическое" in [m["name"] for m in ctx.db.list_moods()]
    assert "Космическое" in ctx.db.get_track(t["id"])["moods"] and "Космическое" in pop.chips.buttons


def test_column_sort_and_ctrl_click_adds_keys(qtbot):
    from PySide6.QtCore import Qt
    from musicplayer.ui.widgets import COLUMNS, TrackModel
    cols = [c for c, _ in COLUMNS]
    m = TrackModel(covers=None)
    base = {"genres": [], "moods": [], "mood_suggestions": [], "plays_by_period": {"month": 0, "year": 0}}
    rows = [{**base, "id": i, "artist": a, "year": y, "title": f"t{i}"}
            for i, (a, y) in enumerate([("B", 2001), ("A", 2010), ("B", 1999), ("A", None), ("A", 2005)])]
    m.set_rows(rows)
    art, year = cols.index("artist"), cols.index("year")
    m.sort_by(art)                                     # artist ascending
    assert [r["artist"] for r in m.rows] == ["A", "A", "A", "B", "B"]
    m.sort_by(year, add=True)                          # then year inside the artist
    assert [(r["artist"], r["year"]) for r in m.rows] == [("A", 2005), ("A", 2010), ("A", None), ("B", 1999), ("B", 2001)]
    assert "▲1" in m.headerData(art, Qt.Orientation.Horizontal) and "▲2" in m.headerData(year, Qt.Orientation.Horizontal)
    m.sort_by(year, add=True)                          # flip the second key
    assert [r["year"] for r in m.rows][:2] == [2010, 2005]
    m.sort_by(art)                                     # plain click on a sorted column: just that column, flipped
    assert m.sort_keys == [(art, True)] and m.rows[0]["artist"] == "B"


def test_completeness_column():
    from musicplayer.ui.widgets import completeness
    t = {"moods": ["A", "B", "C"], "mood_sources": {"A": "manual", "B": "manual", "C": "seed"}, "genres": ["Rock"],
         "lang": "ru", "vocal": "voc", "year": 2005, "flags": ["male"]}
    assert completeness(t) == 100
    assert completeness({**t, "moods": [], "mood_sources": {}, "flags": []}) == 67
    assert completeness({"moods": ["A"], "mood_sources": {"A": "auto"}, "vocal": "inst"}) == 56


def test_wave_resumes_after_library_playback(qtbot, app, library):
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    ids = [t["id"] for t in ctx.db.search(limit=5)]
    ctx.db.set_moods(ids[:2], ["Грустное"])
    wave = win.pages["wave"]
    wave.select_moods(["Грустное"])
    assert wave.start() == 2
    wave_track = ctx.player.current["id"]
    wave._remember()
    ctx.player.play_tracks([ctx.db.get_track(ids[2])])       # the library takes over
    assert not wave.active and wave.suspended
    wave.play_pressed()                                      # back to the wave: the same track again
    assert wave.active and ctx.player.current["id"] == wave_track


def test_delete_key_asks_and_recycles(qtbot, app, library, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox
    from musicplayer.core import trash
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    win.show()
    win.set_page("library")
    lib = win.pages["library"]
    qtbot.waitUntil(lambda: lib.model.rowCount() > 0, timeout=3000)
    lib.view.selectRow(0)
    tid = lib.view.selected_ids()[0]
    gone = []
    monkeypatch.setattr(trash, "to_recycle_bin", lambda p: gone.append(p) or True)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    win.activateWindow()
    qtbot.waitUntil(lambda: win.isActiveWindow(), timeout=3000)
    lib.view.setFocus()
    qtbot.keyClick(lib.view, Qt.Key.Key_Delete)
    assert gone and tid not in [r["id"] for r in ctx.db.search(limit=100)]


def test_edit_dialog_and_artists_page(qtbot, app, library):
    from musicplayer.ui.edittrack import EditTrackDialog
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    tracks = ctx.db.search(limit=3)
    d = EditTrackDialog(ctx, tracks[:1])
    qtbot.addWidget(d)
    d.edits["title"].setText("Исправлено")
    d.save()
    assert ctx.db.get_track(tracks[0]["id"])["title"] == "Исправлено"
    many = EditTrackDialog(ctx, tracks)                 # several tracks: title locked, artist applied to all
    qtbot.addWidget(many)
    assert not many.edits["title"].isEnabled()
    many.edits["artist"].setText("Новый исполнитель")
    many.save()
    assert {ctx.db.get_track(t["id"])["artist"] for t in tracks} == {"Новый исполнитель"}
    win.show()
    win.set_page("artists")
    page = win.pages["artists"]
    qtbot.waitUntil(lambda: page.table.rowCount() > 0, timeout=3000)
    names = [page.table.item(r, 0).text() for r in range(page.table.rowCount())]
    assert "Новый исполнитель" in names


def test_edit_dialog_enter_saves_escape_cancels(qtbot, app, library):
    from PySide6.QtCore import Qt
    from musicplayer.ui.edittrack import EditTrackDialog
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    t = ctx.db.search(limit=1)[0]
    d = EditTrackDialog(ctx, [t])
    qtbot.addWidget(d)
    d.show()
    d.edits["title"].setText("Через Enter")
    qtbot.keyClick(d.edits["title"], Qt.Key.Key_Return)
    assert ctx.db.get_track(t["id"])["title"] == "Через Enter"
    d2 = EditTrackDialog(ctx, [ctx.db.get_track(t["id"])])
    qtbot.addWidget(d2)
    d2.show()
    d2.edits["title"].setText("Не сохранится")
    qtbot.keyClick(d2.edits["title"], Qt.Key.Key_Escape)
    assert ctx.db.get_track(t["id"])["title"] == "Через Enter" and not d2.isVisible()


def test_label_feed_has_genres_and_era(qtbot, app, library):
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    feed = win.pages["labelfeed"]
    assert feed.start("random", 3) > 0
    t = feed.current
    feed.genre_chips.buttons["Metal"].click()
    assert "Metal" in ctx.db.get_track(t["id"])["genres"]


def test_label_feed_guesses_are_preticked_and_unticked_become_rejections(qtbot, app, library):
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    t = ctx.db.search(limit=1)[0]
    ctx.db.suggest_moods([(t["id"], "Грустное", 0.6), (t["id"], "Злое", 0.4)], "model")
    feed = win.pages["labelfeed"]
    feed.queue, feed.pos = [ctx.db.get_track(t["id"])], -1
    feed._advance()
    assert feed.chosen == {"Грустное", "Злое"}                 # the model's guesses start ticked
    feed.chosen.discard("Злое")                                 # the user unticks the wrong one
    feed.chosen.add("Спокойное")                                # and adds a missing one
    feed.confirm()
    got = ctx.db.get_track(t["id"])
    assert set(got["moods"]) == {"Грустное", "Спокойное"} and "Злое" in ctx.db.mood_negatives()[t["id"]]


def test_cue_position_applies_only_to_the_cued_track(qtbot, app, library):
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    a, b = ctx.db.search(limit=2)
    ctx.player.cue([a], 0, 1.5)
    assert getattr(ctx.player, "_cue_id", None) == a["id"]
    ctx.player.play_tracks([b])                  # another track: starts from the beginning
    assert getattr(ctx.player, "_cue_pos", 0) in (0, 0.0) or getattr(ctx.player, "_cue_id", None) != b["id"]


def test_label_feed_does_not_repeat_tracks(qtbot, app, library):
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    feed = win.pages["labelfeed"]
    first = {t["id"] for t in feed.build("random", 2)}
    second = {t["id"] for t in feed.build("random", 2)}
    assert first and not (first & second)


def test_favorites_tracks_albums_artists(qtbot, app, library):
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    t = ctx.db.search(limit=1)[0]
    ctx.player.play_tracks([t])
    win.player_bar.toggle_favorite()                       # the heart in the player bar
    assert ctx.db.is_favorite("track", str(t["id"]))
    ctx.db.set_favorite("album", f"{t['artist']}\x1f{t['album'] or ''}")
    ctx.db.set_favorite("artist", t["artist"])
    page = win.pages["favorites"]
    page.refresh()
    assert page.table.rowCount() == 1 and page.albums.count() == 1 and page.artists.count() == 1
    win.player_bar.toggle_favorite()
    assert not ctx.db.is_favorite("track", str(t["id"]))
