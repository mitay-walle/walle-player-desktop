"""The server's listeners managed by the owner (/api/listeners) and the desktop dialog that shows their QR codes."""
import os

import pytest
from starlette.testclient import TestClient

from musicplayer.server.api import Access, Library, build_app

KEY, GUEST_KEY = "owner-key-123", "guest-key-456"
OWNER = {"Authorization": f"Bearer {KEY}"}
GUEST = {"Authorization": f"Bearer {GUEST_KEY}"}


@pytest.fixture
def server(tmp_path):
    from musicplayer.core.db import Database
    Database(tmp_path / "library.db").close()
    (tmp_path / "covers").mkdir()
    users = [{"id": "owner", "name": "Я", "token": KEY, "owner": True, "can_label": True},
             {"id": "u1", "name": "Аня", "token": GUEST_KEY, "owner": False, "can_label": False}]
    saves = []
    access = Access(users, save=lambda: saves.append([dict(u) for u in users]))
    access.addresses = lambda: {"lan": ["http://192.168.1.5:8780"], "internet": ["https://music.example"]}
    lib = Library(tmp_path / "library.db", tmp_path / "covers")
    yield TestClient(build_app(lib, access)), users, saves
    lib.close()


def test_only_the_owner_sees_and_changes_listeners(server):
    c, users, saves = server
    assert c.get("/api/listeners", headers=GUEST).status_code == 403
    assert c.post("/api/listeners", headers=GUEST, json={"name": "X"}).status_code == 403
    assert c.post("/api/listeners/u1", headers=GUEST, json={"new_key": True}).status_code == 403
    st = c.get("/api/listeners", headers=OWNER).json()
    assert [u["name"] for u in st["users"]] == ["Я", "Аня"]
    assert st["users"][1]["token"] == GUEST_KEY
    assert st["addresses"]["internet"] == ["https://music.example"]
    assert not saves


def test_add_rename_rights_new_key_remove(server):
    c, users, saves = server
    st = c.post("/api/listeners", headers=OWNER, json={"name": "  Alex  ", "can_label": True}).json()
    alex = st["users"][-1]
    assert alex["name"] == "Alex" and alex["can_label"] and alex["token"] and len(users) == 3   # the server's own list
    alex_key = {"Authorization": f"Bearer {alex['token']}"}
    assert c.get("/api/me", headers=alex_key).json()["name"] == "Alex"           # the new key works at once

    c.post(f"/api/listeners/{alex['id']}", headers=OWNER, json={"name": "Алекс", "can_label": False})
    assert users[-1]["name"] == "Алекс" and users[-1]["can_label"] is False
    st = c.post(f"/api/listeners/{alex['id']}", headers=OWNER, json={"new_key": True}).json()
    assert st["users"][-1]["token"] != alex["token"]
    assert c.get("/api/me", headers=alex_key).status_code == 401                # the old key no longer opens
    c.post(f"/api/listeners/{alex['id']}", headers=OWNER, json={"remove": True})
    assert [u["id"] for u in users] == ["owner", "u1"]
    assert len(saves) == 4                                                    # every change is written to remote.json


def test_the_owner_keeps_his_key_and_rights(server):
    c, users, _ = server
    for body in ({"remove": True}, {"new_key": True}, {"can_label": False}):
        assert c.post("/api/listeners/owner", headers=OWNER, json=body).status_code == 400
    assert users[0]["token"] == KEY
    assert c.post("/api/listeners/owner", headers=OWNER, json={"name": "Боб"}).json()["users"][0]["name"] == "Боб"
    assert c.post("/api/listeners/nobody", headers=OWNER, json={"name": "X"}).status_code == 404
    assert c.post("/api/listeners", headers=OWNER, json={"name": " "}).status_code == 400


def test_dialog_shows_the_servers_listeners(qtbot, server, monkeypatch):
    """«Другие устройства» while the library is on a server: its listeners, the internet link first, add / remove."""
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

    from musicplayer.ui.server_listeners import ServerListenersDialog
    c, users, _ = server

    class Lib:
        def request(self, path, body=None, timeout=30):
            r = c.post(path, headers=OWNER, json=body) if body is not None else c.get(path, headers=OWNER)
            r.raise_for_status()
            return r.json()

    class Ctx:
        library = Lib()

    dlg = ServerListenersDialog(Ctx())
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: dlg.user.count() == 2)
    assert dlg.which.itemData(0) == "https://music.example"
    dlg.user.setCurrentIndex(1)
    assert dlg.current_link() == f"https://music.example/?k={GUEST_KEY}"
    dlg._copy()
    assert QApplication.clipboard().text() == dlg.current_link()
    assert dlg.btn_del.isEnabled()                                           # a guest can be removed

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Alex", True)))
    dlg._add()
    qtbot.waitUntil(lambda: dlg.user.count() == 3)
    assert dlg.selected_user()["name"] == "Alex"                              # the new one is shown at once
    assert dlg.current_link() == f"https://music.example/?k={users[-1]['token']}"

    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    dlg._remove()
    qtbot.waitUntil(lambda: dlg.user.count() == 2)
    dlg.user.setCurrentIndex(0)
    assert not dlg.btn_del.isEnabled() and not dlg.btn_key.isEnabled()      # the owner stays


def test_import_likes_into_a_server_listener(server, tmp_path, qtbot, monkeypatch):
    """«Импорт лайков» while the library is on a server: the file is read on the desktop, matched and kept there."""
    from musicplayer.core.db import Database
    c, users, _ = server
    db = Database(tmp_path / "library.db")
    tid = db.upsert_track({"path": str(tmp_path / "a.mp3"), "title": "Весна", "artist": "Кино", "albumartist": "Кино",
                           "album": "А", "year": 2000, "track_no": 1, "disc_no": 1, "duration": 200.0, "mtime": 0,
                           "size": 1, "cover_key": None}, [])
    db.close()
    items = [{"artist": "Кино", "title": "Весна"}, {"artist": "Никто", "title": "Ничего"}]
    assert c.post("/api/listeners/u1/likes", headers=GUEST, json={"items": items}).status_code == 403
    dry = c.post("/api/listeners/u1/likes", headers=OWNER, json={"items": items, "dry_run": True}).json()
    assert dry == {"items": 2, "matched": 1, "unmatched": ["Никто — Ничего"]}
    assert c.get("/api/favorites", headers=GUEST).json()["tracks"] == []
    assert c.post("/api/listeners/u1/likes", headers=OWNER, json={"items": items}).json()["added"] == 1
    assert [t["id"] for t in c.get("/api/favorites", headers=GUEST).json()["tracks"]] == [tid]   # the guest's own likes
    assert c.get("/api/favorites", headers=OWNER).json()["tracks"] == []                          # not the owner's
    assert c.post("/api/listeners/nobody/likes", headers=OWNER, json={"items": items}).status_code == 404

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QFileDialog

    from musicplayer.ui.likeimport_dialog import LikeImportDialog
    from musicplayer.ui.server_listeners import ServerListenersDialog

    class Lib:
        def request(self, path, body=None, timeout=30):
            r = c.post(path, headers=OWNER, json=body) if body is not None else c.get(path, headers=OWNER)
            r.raise_for_status()
            return r.json()

    class Ctx:
        library = Lib()

    f = tmp_path / "likes.txt"
    f.write_text("Кино — Весна\nНикто — Ничего\n", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(f), "")))
    shown = []
    monkeypatch.setattr(LikeImportDialog, "exec", lambda self: (shown.append(self.unmatched), self._import(),
                                                                 LikeImportDialog.DialogCode.Accepted)[-1])
    dlg = ServerListenersDialog(Ctx())
    qtbot.addWidget(dlg)
    qtbot.waitUntil(lambda: dlg.user.count() == 2)
    dlg.user.setCurrentIndex(0)                                       # the owner: his «Избранное» on the server
    dlg._import_likes()
    assert shown == [["Никто — Ничего"]] and "Добавлено лайков: 1" in dlg.status.text()
    assert [t["id"] for t in c.get("/api/favorites", headers=OWNER).json()["tracks"]] == [tid]


def test_open_share_with_a_server_library_has_two_tabs(qtbot, server, tmp_path, monkeypatch):
    """With the library on a server «Другие устройства» opens on its listeners; «Этот компьютер» is the second tab."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from musicplayer.ui.app import create_app
    from musicplayer.ui.server_listeners import ServerListenersDialog
    from musicplayer.ui.share_dialog import ShareDialog
    c, _, _ = server
    _, ctx, win = create_app(home=tmp_path / "home", mcp=False)
    qtbot.addWidget(win)
    monkeypatch.setattr(type(ctx.library), "enabled", property(lambda self: True))
    monkeypatch.setattr(ctx.library, "request", lambda path, body=None, timeout=30:
                        (c.post(path, headers=OWNER, json=body) if body is not None else c.get(path, headers=OWNER)).json())
    shown = []
    monkeypatch.setattr(ServerListenersDialog, "exec", lambda self: shown.append(self))
    win.open_share()
    dlg = shown[0]
    qtbot.addWidget(dlg)
    assert "на сервере" in dlg.windowTitle()
    assert [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())] == ["Сервер", "Этот компьютер"]
    assert isinstance(dlg.pages.widget(1), ShareDialog)
    qtbot.waitUntil(lambda: dlg.user.count() == 2)
    dlg.tabs.setCurrentIndex(1)
    assert dlg.btn_reload.isHidden()
    dlg.done(0)
