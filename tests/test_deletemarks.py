"""«Пометить на удаление» from remote clients; the owner deletes or keeps them on the PC; the list goes to a file."""

from musicplayer.core import deletemarks
from tests.test_app_smoke import app, wait_job  # noqa: F401  (the app fixture)
from tests.test_remote import client, lib  # noqa: F401  (server fixtures)


def test_any_listener_marks_and_nothing_is_deleted_by_the_server(lib, client):
    from tests.test_remote import AUTH, GUEST
    tid = str(lib.ids[0])
    r = client.post("/api/delete-marks", json={"kind": "track", "key": tid}, headers=GUEST)        # a guest may ask
    assert r.status_code == 200 and r.json() == {"ok": True, "kind": "track", "key": tid, "on": True}
    assert client.get("/api/delete-marks", headers=GUEST).json()["items"] == [{"kind": "track", "key": tid}]
    assert client.get("/api/delete-marks", headers=AUTH).json()["items"] == []                      # each sees his own
    album = "Кино\x1fАльбом"
    assert client.post("/api/delete-marks", json={"kind": "album", "key": album}, headers=AUTH).status_code == 200
    assert client.get(f"/api/tracks/{tid}", headers=AUTH).status_code == 200                       # still there
    assert client.post("/api/delete-marks", json={"kind": "track", "key": "999999"}, headers=GUEST).status_code == 404
    assert client.post("/api/delete-marks", json={"kind": "artist", "key": "Кино"}, headers=GUEST).status_code == 400
    r = client.post("/api/delete-marks", json={"kind": "track", "key": tid, "on": False}, headers=GUEST)
    assert r.json()["on"] is False and client.get("/api/delete-marks", headers=GUEST).json()["items"] == []


def test_owner_deletes_or_keeps_on_the_pc_and_the_list_travels(qtbot, app, library, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from musicplayer.core import trash
    from musicplayer.core.db import Database
    from musicplayer.ui.deletemarks_dialog import DeleteMarksDialog
    ctx, win = app
    win.start_scan(str(library))
    wait_job(qtbot, ctx)
    tracks = ctx.db.search(limit=10)
    kino = [t for t in tracks if t["artist"] == "Кино"]
    other = next(t for t in tracks if t["artist"] != "Кино")
    deletemarks.set_mark(ctx.db, "u1", "album", deletemarks.track_album_key(kino[0]))
    deletemarks.set_mark(ctx.db, "u1", "track", str(other["id"]))
    win._update_marks()
    assert win.btn_marks.isVisibleTo(win) and "(2)" in win.btn_marks.text()
    dlg = DeleteMarksDialog(ctx, {"u1": "Аня"}, win)
    qtbot.addWidget(dlg)
    assert dlg.tree.topLevelItemCount() == 2 and "Аня" in dlg.tree.topLevelItem(0).child(0).text(2)
    exported = deletemarks.export(ctx.db, {"u1": "Аня"})                   # to load it elsewhere
    other_db = Database(tmp_path / "elsewhere.db")
    for t in tracks:                                                        # the same music, other paths
        other_db.upsert_track({"path": "D:/copy/" + t["title"] + ".mp3", "title": t["title"], "artist": t["artist"],
                               "albumartist": t.get("albumartist") or "", "album": t["album"]}, [])
    other_db.conn.commit()
    res = deletemarks.load(other_db, exported)
    assert res["marked"] == 3 and not res["unmatched"] and deletemarks.count(other_db) == 2   # the album and the track
    other_db.close()
    track_item = next(dlg.tree.topLevelItem(i).child(0) for i in range(2) if dlg.tree.topLevelItem(i).text(0).startswith("Треки"))
    track_item.setSelected(True)
    dlg.keep_selected()                                                     # «Снять пометку»: kept
    assert deletemarks.count(ctx.db) == 1 and ctx.db.get_track(other["id"]) is not None
    monkeypatch.setattr(trash, "to_recycle_bin", lambda p: True)
    monkeypatch.setattr(QMessageBox, "exec", lambda box: (box.defaultButton().click(), 0)[1])
    dlg.tree.topLevelItem(0).child(0).setSelected(True)
    dlg.delete_selected()                                                   # «Удалить…» the album: marks go along
    assert deletemarks.count(ctx.db) == 0
    assert not {t["id"] for t in kino} & {t["id"] for t in ctx.db.search(limit=100)}
    win._update_marks()
    assert not win.btn_marks.isVisibleTo(win)
