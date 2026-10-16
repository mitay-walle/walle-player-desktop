"""Labels of the playing track on «Моя волна»: a draft, applied to the track / album / artist, «Отмена», delete."""

from tests.test_app_smoke import app, wait_job  # noqa: F401  (the app fixture)


def test_draft_apply_cancel_and_delete(qtbot, app, library, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from musicplayer.core import trash
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    kino = [t for t in ctx.db.search(limit=100) if t["artist"] == "Кино"]
    ctx.player.play_tracks(kino, 0)
    panel = win.pages["wave"].labels
    first = panel.track
    assert first["id"] == kino[0]["id"] and "К альбому (2)" == panel.btn_album.text()
    mood = next(iter(panel.moods.buttons))
    panel.moods.buttons[mood].click()                                       # a draft: nothing written yet
    panel.genres.buttons["Jazz"].click()
    assert panel.dirty() and panel.btn_track.isEnabled()
    assert mood not in ctx.db.get_track(first["id"])["moods"]
    panel.btn_cancel.click()                                                # «Отмена»: back to what the track has
    assert not panel.dirty() and not panel.moods.buttons[mood].isChecked()
    panel.moods.buttons[mood].click()
    panel.btn_track.click()                                                 # «К треку»
    assert mood in ctx.db.get_track(first["id"])["moods"] and mood not in ctx.db.get_track(kino[1]["id"])["moods"]
    panel.genres.buttons["Jazz"].click()
    panel.moods.buttons[mood].click()                                       # taken off here ...
    panel.btn_album.click()                                                 # ... and Jazz added: the whole album
    for t in kino:
        got = ctx.db.get_track(t["id"])
        assert "Jazz" in got["genres"] and mood not in got["moods"]
    assert "альбому" in win.pages["wave"].toast.text()
    monkeypatch.setattr(trash, "to_recycle_bin", lambda p: True)
    monkeypatch.setattr(QMessageBox, "exec", lambda box: (box.defaultButton().click(), 0)[1])
    panel.btn_delete.click()                                                # «Удалить трек…»
    assert first["id"] not in [t["id"] for t in ctx.db.search(limit=100)]
    assert panel.track is None or panel.track["id"] != first["id"]


def test_player_bar_links_and_selectable_wave_text(qtbot, app, library):
    from PySide6.QtCore import Qt
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    kino = [t for t in ctx.db.search(limit=100) if t["artist"] == "Кино"]
    ctx.player.play_tracks(kino, 0)
    sub = win.player_bar.sub
    assert 'href="artist"' in sub.text() and 'href="album"' in sub.text()
    shown = []
    ctx.events.show_rows.connect(lambda rows, title: shown.append((len(rows), title)))
    sub.linkActivated.emit("album")                                         # «Группа крови»: its tracks
    sub.linkActivated.emit("artist")
    assert shown == [(2, f"Альбом «{kino[0]['album']}»"), (2, "Кино")]
    assert win.pages["library"].mode_title == "Кино"
    wave = win.pages["wave"]
    for w in (wave.title, wave.sub):
        assert w.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
