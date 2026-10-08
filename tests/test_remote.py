"""Library server for other devices: access key, catalogue, audio with Range, «Моя волна», plays, likes."""

import json
import socket

import pytest
from starlette.testclient import TestClient

from musicplayer.server import config
from musicplayer.server.api import FAIL_MAX, Access, Library, build_app
from musicplayer.server.runner import DISCOVER, RemoteServer

KEY, GUEST_KEY = "test-key-123", "guest-key-456"
AUTH = {"Authorization": f"Bearer {KEY}"}
GUEST = {"Authorization": f"Bearer {GUEST_KEY}"}
USERS = [{"id": "owner", "name": "Я", "token": KEY, "owner": True, "can_label": True},
         {"id": "u1", "name": "Аня", "token": GUEST_KEY, "owner": False, "can_label": False}]


@pytest.fixture
def lib(tmp_path):
    from musicplayer.core.db import Database
    db = Database(tmp_path / "library.db")
    audio = tmp_path / "a.mp3"
    audio.write_bytes(bytes(range(256)) * 40)
    ids = []
    for i, (title, artist, album) in enumerate([("Весна", "Кино", "Альбом"), ("Зима", "Кино", "Альбом"),
                                                ("Summer", "Other", "Hits")]):
        ids.append(db.upsert_track({"path": str(audio if i == 0 else tmp_path / f"missing{i}.mp3"), "title": title,
                                    "artist": artist, "albumartist": artist, "album": album, "year": 2000 + i,
                                    "track_no": i + 1, "disc_no": 1, "duration": 200.0, "mtime": 0, "size": 1,
                                    "cover_key": None}, ["Рок"]))
    db.set_moods([ids[0]], ["Весёлое"])
    db.suggest_moods([(ids[1], "Весёлое", 0.9)], source="model")
    db.close()
    covers = tmp_path / "covers"
    covers.mkdir()
    (covers / "abcdef0123.jpg").write_bytes(b"\xff\xd8jpeg")
    changed = []
    lib = Library(tmp_path / "library.db", covers, on_change=changed.extend)
    lib.ids, lib.changed_ids = ids, changed
    yield lib
    lib.close()


@pytest.fixture
def client(lib):
    return TestClient(build_app(lib, Access(USERS), {"name": "test"}))


def test_key_required(client):
    assert client.get("/api/ping").json()["authorized"] is False
    assert client.get("/api/info").status_code == 401
    assert client.get("/api/info", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/info", headers=AUTH).status_code == 200
    assert client.get(f"/api/info?k={KEY}").status_code == 200


def test_qr_link_sets_cookie(client):
    r = client.get(f"/?k={KEY}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "wp_k=" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]
    assert client.get("/api/info").status_code == 200           # the cookie now opens the API
    assert client.get("/").status_code == 200 and b"Walle" in client.get("/").content


def test_brute_force_bans_the_address(lib, tmp_path):
    """fail2ban: FAIL_MAX wrong keys ban the address (everything answers 429), longer on a repeat, kept on disk;
    an IPv6 ban covers its /64; the owner can lift it; the computer itself is never banned."""
    from musicplayer.server.api import BAN_BASE, BAN_FACTOR
    bans = tmp_path / "bans.json"
    shown = []
    access = Access(USERS, ban_file=bans, on_ban=shown.append)
    c = TestClient(build_app(lib, access), client=("203.0.113.7", 5000))
    other = TestClient(build_app(lib, access), client=("203.0.113.8", 5000))
    for _ in range(FAIL_MAX - 1):
        c.get("/api/info", headers={"Authorization": "Bearer nope"})
    assert c.get("/api/info", headers=AUTH).status_code == 200            # not yet
    c.get("/api/info", headers={"Authorization": "Bearer nope"})
    r = c.get("/api/ping")
    assert r.status_code == 429 and int(r.headers["retry-after"]) > BAN_BASE - 60   # banned: even the right key, ping
    assert c.get("/api/info", headers=AUTH).status_code == 429
    assert other.get("/api/info", headers=AUTH).status_code == 200        # another address is fine
    assert shown and shown[0]["key"] == "203.0.113.7" and shown[0]["strikes"] == 1
    again = Access(USERS, ban_file=bans)                                   # survives a restart
    assert again.blocked("203.0.113.7")
    access.unban("203.0.113.7")
    assert c.get("/api/info", headers=AUTH).status_code == 200
    for _ in range(FAIL_MAX):
        c.get("/api/info", headers={"Authorization": "Bearer nope"})
    b2 = access.banned()[0]
    assert b2["strikes"] == 2 and b2["until"] - b2["since"] > BAN_BASE * BAN_FACTOR - 60   # a repeat is longer
    v6 = TestClient(build_app(lib, access), client=("2a00:1450:4010:c05::71", 5000))
    for _ in range(FAIL_MAX):
        v6.get("/api/info", headers={"Authorization": "Bearer nope"})
    assert access.blocked("2a00:1450:4010:c05::ffff") and not access.blocked("2a00:1450:4010:c06::1")   # the /64
    for _ in range(FAIL_MAX + 3):
        access.failed("127.0.0.1")
    assert not access.blocked("127.0.0.1")                                 # never the computer itself
    access.unban()
    assert access.banned() == []


def test_catalogue(client, lib):
    info = client.get("/api/info", headers=AUTH).json()
    assert info["tracks"] == 3 and info["name"] == "test"
    assert any(m["name"] == "Весёлое" and m["color"].startswith("#") for m in info["moods"])
    items = client.get("/api/tracks?q=кино", headers=AUTH).json()["items"]
    assert {t["title"] for t in items} == {"Весна", "Зима"}
    t = client.get(f"/api/tracks/{lib.ids[0]}", headers=AUTH).json()
    assert t["moods"] == ["Весёлое"] and t["stream"] == f"/api/stream/{lib.ids[0]}" and t["liked"] is False
    assert client.get("/api/tracks?mood=Весёлое", headers=AUTH).json()["items"][0]["id"] == lib.ids[0]
    assert len(client.get("/api/artists", headers=AUTH).json()["items"]) == 2
    assert {a["name"] for a in client.get("/api/albums", headers=AUTH).json()["items"]} == {"Альбом", "Hits"}
    assert client.get("/api/tracks/99999", headers=AUTH).status_code == 404


def test_stream_range_and_missing_file(client, lib):
    r = client.get(f"/api/stream/{lib.ids[0]}", headers=AUTH | {"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == bytes(range(10, 20)) and r.headers["content-type"] == "audio/mpeg"
    assert client.get(f"/api/stream/{lib.ids[1]}", headers=AUTH).status_code == 410


def test_cover_rejects_paths(client):
    assert client.get("/api/cover/abcdef0123", headers=AUTH).content == b"\xff\xd8jpeg"
    assert client.get("/api/cover/..%2Flibrary", headers=AUTH).status_code == 404


def test_guest_has_own_likes_and_plays_and_cannot_label(client, lib):
    tid = lib.ids[2]
    assert client.get("/api/me", headers=GUEST).json() == {"name": "Аня", "owner": False, "can_label": False}
    client.post("/api/favorites", json={"kind": "track", "id": tid}, headers=GUEST)
    client.post("/api/plays", json={"id": tid, "listened": 100}, headers=GUEST)
    assert [t["id"] for t in client.get("/api/favorites", headers=GUEST).json()["tracks"]] == [tid]
    assert client.get("/api/favorites", headers=AUTH).json()["tracks"] == []           # the owner's are untouched
    g, o = (client.get(f"/api/tracks/{tid}", headers=h).json() for h in (GUEST, AUTH))
    assert (g["liked"], g["plays"]) == (True, 1) and (o["liked"], o["plays"]) == (False, 0)
    assert [t["id"] for t in client.get("/api/recent", headers=GUEST).json()["items"]] == [tid]
    assert client.get("/api/recent", headers=AUTH).json()["items"] == []
    assert lib.changed_ids == []                             # guest activity does not touch the desktop app
    assert client.post(f"/api/tracks/{tid}/genres", json={"genres": ["Pop"]}, headers=GUEST).status_code == 403
    assert client.post("/api/wave/rate", json={"id": tid, "verdict": "like", "kind": "guess", "moods": ["Весёлое"]},
                       headers=GUEST).status_code == 403
    USERS[1]["can_label"] = True                              # the owner allowed it in the dialog: works at once
    try:
        assert client.post(f"/api/tracks/{tid}/genres", json={"genres": ["Pop"]}, headers=GUEST).json()["genres"] == ["Pop"]
    finally:
        USERS[1]["can_label"] = False


def test_listener_renames_himself(lib):
    users = [dict(u) for u in USERS]
    saved = []
    c = TestClient(build_app(lib, Access(users, save=lambda: saved.append(users[1]["name"]))))
    assert c.post("/api/me", json={"name": "  Анна   Петровна "}, headers=GUEST).json()["name"] == "Анна Петровна"
    assert users[1]["name"] == "Анна Петровна" and saved == ["Анна Петровна"]
    assert c.post("/api/me", json={"name": " "}, headers=GUEST).status_code == 400
    assert c.get("/api/me", headers=AUTH).json()["name"] == "Я"


def test_plays_and_likes(client, lib):
    tid = lib.ids[2]
    assert client.post("/api/plays", json={"id": tid, "listened": 120}, headers=AUTH).json() == {"ok": True}
    assert client.get(f"/api/tracks/{tid}", headers=AUTH).json()["plays"] == 1
    assert [t["id"] for t in client.get("/api/recent", headers=AUTH).json()["items"]] == [tid]
    client.post("/api/favorites", json={"kind": "track", "id": tid}, headers=AUTH)
    client.post("/api/favorites", json={"kind": "album", "artist": "Кино", "album": "Альбом"}, headers=AUTH)
    fav = client.get("/api/favorites", headers=AUTH).json()
    assert [t["id"] for t in fav["tracks"]] == [tid] and fav["albums"] == [{"artist": "Кино", "name": "Альбом"}]
    assert client.get(f"/api/tracks/{tid}", headers=AUTH).json()["liked"] is True
    client.post("/api/favorites", json={"kind": "track", "id": tid, "on": False}, headers=AUTH)
    assert client.get("/api/favorites", headers=AUTH).json()["tracks"] == []
    assert tid in lib.changed_ids                            # the desktop app is told to refresh


def test_wave_rate_teaches_the_model(client, lib):
    items = client.post("/api/wave/next", json={"moods": ["Весёлое"], "n": 5}, headers=AUTH).json()["items"]
    kinds = {t["id"]: t["wave"]["kind"] for t in items}
    assert kinds == {lib.ids[0]: "known", lib.ids[1]: "guess"}
    r = client.post("/api/wave/rate", json={"id": lib.ids[1], "verdict": "like", "kind": "guess", "moods": ["Весёлое"]},
                    headers=AUTH).json()
    assert r == {"result": "confirmed", "moods": ["Весёлое"]}
    assert client.get(f"/api/tracks/{lib.ids[1]}", headers=AUTH).json()["moods"] == ["Весёлое"]


def test_fix_moods(client, lib):
    t = client.post(f"/api/tracks/{lib.ids[1]}/moods", json={"moods": ["Грустное"], "reject": ["Весёлое"]},
                    headers=AUTH).json()
    assert t["moods"] == ["Грустное"] and t["suggested"] == []


def test_config_owner_and_listeners(tmp_path):
    cfg = config.load(tmp_path)
    assert len(cfg.owner["token"]) >= 20 and cfg.owner["can_label"] and not cfg.enabled
    assert len(cfg.library_id) == 16 and config.load(tmp_path).library_id == cfg.library_id   # stable: clients find it again
    assert config.load(tmp_path).owner["token"] == cfg.owner["token"]
    u = config.add_user(cfg, "Аня")
    config.save(tmp_path, cfg)
    again = config.load(tmp_path)
    assert again.user(u["id"])["name"] == "Аня" and not again.user(u["id"])["can_label"]
    config.remove_user(again, "owner")                       # the owner cannot be removed
    assert again.owner
    (tmp_path / "remote.json").write_text(json.dumps({"token": "old-single-key"}))   # the first version's file
    assert config.load(tmp_path).owner["token"] == "old-single-key"


def free_port() -> int:
    """A port free for both TCP (HTTP) and UDP (discovery)."""
    while True:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as u:
                u.bind(("0.0.0.0", port))
            return port
        except OSError:
            continue


def test_server_thread_and_discovery(lib, tmp_path):
    port = free_port()
    srv = RemoteServer(lib.db.path, lib.covers, USERS, port, host="127.0.0.1", name="pc", library_id="lib123")
    assert srv.start(), srv.error
    try:
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/info", headers=AUTH)
        assert json.loads(urllib.request.urlopen(req, timeout=5).read())["tracks"] == 3
        assert srv.clients() and srv.clients()[0]["address"] == "127.0.0.1" and srv.clients()[0]["user"] == "Я"
        assert srv.pair_url("http://x:1", USERS[1]) == f"http://x:1/?k={GUEST_KEY}"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as u:
            u.settimeout(2)
            u.sendto(DISCOVER, ("127.0.0.1", port))
            reply = json.loads(u.recvfrom(512)[0])
        assert reply["app"] == "WallePlayer" and reply["port"] == port and reply["name"] == "pc" and reply["library"] == "lib123"
        ping = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=5).read())
        assert ping["library"] == "lib123"
        busy = RemoteServer(lib.db.path, lib.covers, USERS, port, host="127.0.0.1")
        assert not busy.start(wait=3) and busy.error              # the port is taken: a clear error, no hang
    finally:
        srv.stop()


def test_share_dialog_in_app(qtbot, tmp_path, monkeypatch):
    """The checkbox starts the server, every listener has an own QR link; adding/removing listeners and new keys
    take effect without a restart."""
    from musicplayer.server import runner
    from musicplayer.ui.app import create_app
    from musicplayer.ui.share_dialog import ShareDialog
    from musicplayer.ui import share_dialog
    monkeypatch.setattr(runner, "lan_addresses", lambda: ["127.0.0.1"])
    monkeypatch.setattr(share_dialog, "lan_addresses", lambda: ["127.0.0.1"])
    monkeypatch.setattr(share_dialog, "network_problems", lambda ip, program=None: ["Wi-Fi общественная"])
    _, ctx, win = create_app(home=tmp_path / "home", mcp=False)
    qtbot.addWidget(win)
    sh = win.sharing
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        sh.cfg.port = s.getsockname()[1]
    dlg = ShareDialog(sh, win)
    qtbot.addWidget(dlg)
    assert not sh.running and dlg.qr.pixmap().isNull()
    dlg.enable.setChecked(True)
    assert sh.running, sh.server.error
    qtbot.waitUntil(lambda: not dlg.warn.isHidden() and "общественная" in dlg.warn.text())   # why a phone may not see it
    assert dlg.current_link().endswith("/?k=" + sh.cfg.owner["token"]) and not dlg.qr.pixmap().isNull()
    assert config.load(ctx.home).enabled                         # remembered: starts with the app next time
    guest = sh.add_user("Аня")
    qtbot.waitUntil(lambda: dlg.user.findData(guest["id"]) >= 0)
    dlg.user.setCurrentIndex(dlg.user.findData(guest["id"]))
    assert dlg.current_link().endswith("/?k=" + guest["token"]) and dlg.btn_del.isEnabled()
    assert sh.server.access.user_for(guest["token"])["name"] == "Аня"
    old = guest["token"]
    sh.new_key(guest["id"])
    assert sh.server.access.user_for(old) is None and sh.server.access.user_for(guest["token"])
    dlg.can_label.setChecked(True)
    assert config.load(ctx.home).user(guest["id"])["can_label"]
    for _ in range(10):                                            # a guesser is banned and shown; «Снять» lifts it
        sh.server.access.failed("198.51.100.9")
    dlg._refresh_clients()
    assert not dlg.bans.isHidden() and "198.51.100.9" in dlg.bans.text()
    dlg._unban()
    assert dlg.bans.isHidden() and not sh.server.access.blocked("198.51.100.9")
    sh.rename_user(guest["id"], "Анна")
    assert sh.server.access.user_for(guest["token"])["name"] == "Анна" and config.load(ctx.home).user(guest["id"])["name"] == "Анна"
    sh.remove_user(guest["id"])
    assert sh.server.access.user_for(guest["token"]) is None
    dlg.enable.setChecked(False)
    assert not sh.running and not config.load(ctx.home).enabled
    dlg.close()
    win.close()


def test_model_confidence(client, lib):
    """Moods the model set by itself carry its confidence; /probs is empty without a model, not an error."""
    tid = lib.ids[2]
    lib(lambda db: db.suggest_moods([(tid, "Грустное", 0.83)], source="auto"))
    t = client.get(f"/api/tracks/{tid}", headers=AUTH).json()
    assert t["moods"] == ["Грустное"] and t["mood_conf"] == {"Грустное": 0.83}
    assert client.get(f"/api/tracks/{lib.ids[0]}", headers=AUTH).json()["mood_conf"] == {}   # the user's label
    assert client.get(f"/api/tracks/{tid}/probs", headers=AUTH).json() == {"probs": {}}


def test_labels_like_the_desktop_feed(client, lib):
    tid = lib.ids[2]
    info = client.get("/api/info", headers=AUTH).json()["labels"]
    assert info["eras"][0] == 1960 and any(f["name"] == "Punk" and "Pop punk" in f["subs"] for f in info["genre_tree"])
    t = client.post(f"/api/tracks/{tid}/labels", json={"moods": ["Весёлое"], "genres": ["Punk", "Pop punk"],
                                                        "flags": ["live"], "lang": "en", "vocal": "voc", "era": 1990},
                    headers=AUTH).json()
    assert (t["moods"], t["genres"], t["flags"], t["lang"], t["vocal"], t["era"]) == \
        (["Весёлое"], ["Punk", "Pop punk"], ["live"], "en", "voc", 1990)
    t = client.post(f"/api/tracks/{tid}/labels", json={"era": None, "lang": None}, headers=AUTH).json()
    assert t["era"] is None and t["lang"] is None and t["genres"] == ["Punk", "Pop punk"]   # other labels kept
    assert client.post(f"/api/tracks/{tid}/labels", json={"flags": ["nope"]}, headers=AUTH).status_code == 400
    assert client.post(f"/api/tracks/{tid}/labels", json={"era": 2000}, headers=GUEST).status_code == 403


def test_web_player_files_for_offline(client):
    """The service worker is served from the root (its scope is the whole player); the scripts are always revalidated."""
    r = client.get("/sw.js")
    assert r.status_code == 200 and "javascript" in r.headers["content-type"] and r.headers["cache-control"] == "no-cache"
    page = client.get("/").text
    assert "/static/offline.js" in page and page.index("offline.js") < page.index("app.js")
    assert client.get("/static/offline.js").headers["cache-control"] == "no-cache"


def test_lookup_refreshes_saved_tracks(client, lib):
    """Fresh moods/genres/likes of many tracks at once (for tracks saved on the device); unknown ids are left out."""
    lib(lambda db: db.set_moods([lib.ids[2]], ["Грустное"]))
    client.post("/api/favorites", json={"kind": "track", "id": lib.ids[0]}, headers=AUTH)
    items = client.post("/api/tracks/lookup", json={"ids": [lib.ids[0], lib.ids[2], 999999]}, headers=AUTH).json()["items"]
    got = {t["id"]: t for t in items}
    assert set(got) == {lib.ids[0], lib.ids[2]}
    assert got[lib.ids[2]]["moods"] == ["Грустное"] and got[lib.ids[0]]["liked"] is True


def test_busy_port_takes_the_next_one(qtbot, tmp_path, monkeypatch):
    """Another program (e.g. a test server) holds the port: the app starts on the next free one and says so."""
    from musicplayer.server import runner
    from musicplayer.ui import share_dialog
    from musicplayer.ui.app import create_app
    monkeypatch.setattr(runner, "lan_addresses", lambda: ["127.0.0.1"])
    monkeypatch.setattr(share_dialog, "network_problems", lambda ip, program=None: [])
    _, ctx, win = create_app(home=tmp_path / "home", mcp=False)
    qtbot.addWidget(win)
    sh = win.sharing
    while True:
        port = free_port()
        if runner.port_free(port + 1):
            break
    holder = socket.socket()
    holder.bind(("0.0.0.0", port))
    holder.listen()
    try:
        sh.cfg.port = port
        assert sh.start(), sh.server.error
        assert sh.server.port == port + 1 and str(port) in sh.notice
        assert sh.cfg.port == port and config.load(ctx.home).port == port     # saved links keep the usual port
    finally:
        sh.stop()
        holder.close()
        win.close()


def test_new_device_asks_for_access(lib):
    """No key: the device asks under a name, the owner approves in the app, the device gets its key (once)."""
    users = [dict(u) for u in USERS]
    shown = []
    access = Access(users, on_join=shown.append)
    c = TestClient(build_app(lib, access))
    r = c.post("/api/join", json={"name": " Петя "}, headers={"User-Agent": "Mozilla (Linux; Android 14)"}).json()
    assert shown and shown[0]["name"] == "Петя" and shown[0]["device"] == "Android" and "secret" not in shown[0]
    state = f"/api/join/{r['id']}?s={r['secret']}"
    assert c.get(state).json() == {"status": "pending"}
    assert c.get(f"/api/join/{r['id']}?s=wrong").status_code == 404
    assert access.answer_join(r["id"], users[1])
    ok = c.get(state)
    assert ok.json() == {"status": "approved", "key": GUEST_KEY, "user": "Аня"} and "wp_k=" in ok.headers["set-cookie"]
    assert c.get("/api/me").json()["name"] == "Аня"                 # the cookie opens the API
    assert c.get(state).status_code == 404                          # the key is handed out once
    r2 = c.post("/api/join", json={"name": "Вася"}).json()
    access.answer_join(r2["id"], None)
    assert c.get(f"/api/join/{r2['id']}?s={r2['secret']}").json() == {"status": "denied"}
    assert c.post("/api/join", json={"name": ""}).status_code == 400
    for _ in range(3):
        c.post("/api/join", json={"name": "спам"})
    assert c.post("/api/join", json={"name": "спам"}).status_code == 429


def test_access_requests_can_be_off(lib):
    c = TestClient(build_app(lib, Access(USERS)))                    # the headless server has nobody to ask
    assert c.post("/api/join", json={"name": "Петя"}).status_code == 403


def test_join_dialog_lets_a_device_in(qtbot, tmp_path, monkeypatch):
    """A request from a device opens the app's window; «Разрешить» makes a new listener (or picks an existing one)."""
    from musicplayer.server import runner
    from musicplayer.ui import share_dialog
    from musicplayer.ui.app import create_app
    monkeypatch.setattr(runner, "lan_addresses", lambda: ["127.0.0.1"])
    monkeypatch.setattr(share_dialog, "network_problems", lambda ip, program=None: [])
    _, ctx, win = create_app(home=tmp_path / "home", mcp=False)
    qtbot.addWidget(win)
    sh = win.sharing
    sh.cfg.port = free_port()
    assert sh.start(), sh.server.error
    try:
        opened = []
        monkeypatch.setattr(share_dialog.JoinDialog, "show", lambda self: opened.append(self))
        req = sh.server.access.request_join("Петя", "Android", "192.168.1.7")
        sh.join_requested.emit({k: v for k, v in req.items() if k != "secret"})
        qtbot.waitUntil(lambda: bool(opened))
        dlg = opened[0]
        assert dlg.who.currentData() == "new"
        dlg.can_label.setChecked(True)
        dlg._answer("new")
        user = next(u for u in sh.cfg.users if u["name"] == "Петя")
        assert user["can_label"] and config.load(ctx.home).user(user["id"])
        assert sh.server.access.join_state(req["id"], req["secret"])["user"]["token"] == user["token"]
        req2 = sh.server.access.request_join("мой планшет", "iPad", "192.168.1.8")
        sh.answer_join(req2, sh.cfg.owner["id"])                       # the owner's own second device
        assert sh.server.access.join_state(req2["id"], req2["secret"])["user"]["owner"]
        assert len(sh.cfg.users) == 2
    finally:
        sh.stop()
        win.close()


def test_tray_keeps_the_server_when_the_window_closes(qtbot, tmp_path, monkeypatch):
    """Library open for devices: closing the window hides it to the tray (server and app go on); «Выход» quits."""
    from PySide6.QtWidgets import QApplication
    from musicplayer.server import runner
    from musicplayer.ui import share_dialog, tray
    from musicplayer.ui.app import create_app
    monkeypatch.setattr(runner, "lan_addresses", lambda: ["127.0.0.1"])
    monkeypatch.setattr(share_dialog, "network_problems", lambda ip, program=None: [])
    monkeypatch.setattr(tray.Tray, "available", property(lambda self: True))
    monkeypatch.setattr(QApplication, "quit", lambda *a: None)
    _, ctx, win = create_app(home=tmp_path / "home", mcp=False)
    qtbot.addWidget(win)
    win.tray = tray.Tray(win)
    win.show()
    win.sharing.cfg.port = free_port()
    assert win.sharing.start(), win.sharing.server.error
    win.close()
    assert not win.isVisible() and win.sharing.running and not ctx.closed      # only hidden
    qtbot.waitUntil(lambda: "Открыто для устройств" in win.tray.a_state.text())
    win.tray.quit()
    assert ctx.closed and not win.sharing.running
    assert not tray.autostart_supported() and not tray.set_autostart(True)      # only the built exe starts with Windows


def test_versions_and_mobile_update(client, lib):
    """Clients compare versions; the newest mobile app put into <data>/mobile is offered and downloaded with a key."""
    from musicplayer import __version__
    p = client.get("/api/ping").json()
    assert p["version"] == __version__ and p["api"] >= p["api_min"] and p["mobile"] is None
    folder = lib.models.parent / "mobile"
    folder.mkdir()
    (folder / "WallePlayer-1.0.9.apk").write_bytes(b"old")
    (folder / "WallePlayer-1.0.10.apk").write_bytes(b"new")
    m = client.get("/api/info", headers=AUTH).json()["mobile"]
    assert (m["version"], m["url"], m["size"]) == ("1.0.10", "/api/app/WallePlayer-1.0.10.apk", 3)   # 1.0.10 > 1.0.9
    assert client.get(m["url"]).status_code == 401
    assert client.get(m["url"], headers=AUTH).content == b"new"
    assert client.get("/api/app/WallePlayer-1.0.9.apk", headers=AUTH).status_code == 404   # only the newest


def test_mobile_requests(client, lib):
    """Re-sent plays (same uid) count once; cover thumbnails; list fingerprints for offline sync."""
    tid = lib.ids[2]
    assert client.post("/api/plays", json={"id": tid, "listened": 100, "uid": "p-1"}, headers=GUEST).json() == {"ok": True}
    assert client.post("/api/plays", json={"id": tid, "listened": 100, "uid": "p-1"}, headers=GUEST).json()["duplicate"]
    assert client.get(f"/api/tracks/{tid}", headers=GUEST).json()["plays"] == 1
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (600, 600), (200, 30, 30)).save(buf, "JPEG")
    (lib.covers / "abcdef0123.jpg").write_bytes(buf.getvalue())
    small = client.get("/api/cover/abcdef0123?size=150", headers=AUTH)
    assert Image.open(io.BytesIO(small.content)).size == (160, 160)          # the nearest standard size
    assert Image.open(io.BytesIO(client.get("/api/cover/abcdef0123", headers=AUTH).content)).size == (600, 600)
    r1 = client.get("/api/favorites", headers=AUTH).json()["rev"]
    client.post("/api/favorites", json={"kind": "track", "id": tid}, headers=AUTH)
    assert client.get("/api/favorites", headers=AUTH).json()["rev"] != r1


def test_config_never_loses_keys(tmp_path):
    """A truncated settings file (the app killed mid-write) is restored from the backup, not replaced by new keys."""
    cfg = config.load(tmp_path)
    token, lib_id = cfg.owner["token"], cfg.library_id
    config.add_user(cfg, "Аня")
    config.save(tmp_path, cfg)                       # remote.json.bak now holds the previous good state
    config.save(tmp_path, cfg)
    (tmp_path / "remote.json").write_text('{"enabled": tr', encoding="utf-8")       # cut in the middle
    again = config.load(tmp_path)
    assert again.owner["token"] == token and again.library_id == lib_id and len(again.users) == 2
    (tmp_path / "remote.json").write_text("garbage", encoding="utf-8")
    (tmp_path / "remote.json.bak").write_text("garbage", encoding="utf-8")
    config.load(tmp_path)
    assert list(tmp_path.glob("remote.json.broken-*"))                              # kept aside, not destroyed


def test_guest_order_and_wave_follow_his_own_plays(client, lib):
    """«Часто слушаю» / «Незнакомое» for a guest come from HIS plays, not the owner's."""
    a, b, c = lib.ids
    for _ in range(3):
        client.post("/api/plays", json={"id": a, "listened": 100}, headers=AUTH)     # the owner loves track a
    client.post("/api/plays", json={"id": c, "listened": 100}, headers=GUEST)        # the guest listened to c
    top_owner = client.get("/api/tracks?order=play_count&desc=1&limit=1", headers=AUTH).json()["items"][0]["id"]
    top_guest = client.get("/api/tracks?order=play_count&desc=1&limit=1", headers=GUEST).json()["items"][0]
    assert top_owner == a and top_guest["id"] == c and top_guest["plays"] == 1
    from musicplayer.core.wave import Wave
    plays = lib.guest_plays({"id": "u1"})
    rows = lib(lambda db: db.search(limit=100))
    w = Wave(lib.db, character="favorite", plays=plays)._weights(rows)
    by_id = dict(zip([r["id"] for r in rows], w))
    assert by_id[a] == by_id[b]                                     # the owner's plays do not count for the guest


def test_server_answers_over_ipv6_too(lib):
    """Listening on all interfaces means IPv4 and IPv6: phones from the internet come over IPv6 (grey IPv4 at home)."""
    import urllib.request
    port = free_port()
    srv = RemoteServer(lib.db.path, lib.covers, USERS, port)          # host 0.0.0.0 = everything
    assert srv.start(), srv.error
    try:
        for host in ("127.0.0.1", "[::1]"):
            ping = json.loads(urllib.request.urlopen(f"http://{host}:{port}/api/ping", timeout=5).read())
            assert ping["app"] == "WallePlayer", host
        busy = RemoteServer(lib.db.path, lib.covers, USERS, port)
        assert not busy.start() and busy.port_busy
    finally:
        srv.stop()


class FakeRouter:
    def __init__(self, ip):
        self.ip, self.opened, self.closed = ip, [], []

    def external_ip(self):
        return self.ip

    def add_port(self, external, internal, lan):
        self.opened.append((external, internal, lan))

    def remove_port(self, external):
        self.closed.append(external)


def make_sharing(qtbot, tmp_path, monkeypatch):
    from musicplayer.server import runner
    from musicplayer.ui import share_dialog
    from musicplayer.ui.app import create_app
    monkeypatch.setattr(runner, "lan_addresses", lambda: ["192.168.10.102"])
    monkeypatch.setattr(share_dialog, "network_problems", lambda ip, program=None: [])
    monkeypatch.setattr(share_dialog, "global_ipv6", lambda: [])
    _, ctx, win = create_app(home=tmp_path / "home", mcp=False)
    qtbot.addWidget(win)
    win.sharing.cfg.port = free_port()
    return win, win.sharing, share_dialog


def test_static_ip_opens_the_port_or_warns_about_grey_ip(qtbot, tmp_path, monkeypatch):
    win, sh, share_dialog = make_sharing(qtbot, tmp_path, monkeypatch)
    white = FakeRouter("93.184.216.34")
    monkeypatch.setattr(share_dialog.Router, "find", classmethod(lambda cls, lan=None, timeout=3: white))
    sh.cfg.net_mode, sh.cfg.static_port = "static", 8780
    assert sh.start(), sh.server.error
    try:
        qtbot.waitUntil(lambda: bool(white.opened), timeout=5000)
        assert white.opened == [(8780, sh.server.port, "192.168.10.102")]
        assert sh.public_base() == "http://93.184.216.34:8780" and "открыт" in sh.net_state
        assert sh.bases()[0] == ("Из интернета (статический IP)", "http://93.184.216.34:8780")
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{sh.server.port}/api/info",
                                     headers={"Authorization": "Bearer " + sh.cfg.owner["token"]})
        addr = json.loads(urllib.request.urlopen(req, timeout=5).read())["addresses"]
        assert addr["internet"] == ["http://93.184.216.34:8780"] and addr["lan"][0].startswith("http://192.168.10.102:")
        grey = FakeRouter("100.69.234.96")
        monkeypatch.setattr(share_dialog.Router, "find", classmethod(lambda cls, lan=None, timeout=3: grey))
        sh.set_net(static_host="music.example.ru")
        qtbot.waitUntil(lambda: "серый" in sh.net_state, timeout=5000)
        assert white.closed == [8780] and grey.opened == []                # the old mapping closed, none on grey
        assert sh.public_base() == "http://music.example.ru:8780"          # a DDNS name wins over the router's IP
        sh.set_net(static_host="")
        qtbot.waitUntil(lambda: "серый" in sh.net_state, timeout=5000)
        assert sh.public_base() is None                                     # no grey address in the QR
    finally:
        sh.stop()
        win.close()


def test_relay_reports_an_unreachable_server_and_bans_briefly(qtbot, tmp_path, monkeypatch):
    win, sh, _ = make_sharing(qtbot, tmp_path, monkeypatch)
    sh.cfg.net_mode, sh.cfg.relay_host, sh.cfg.relay_ssh_port, sh.cfg.relay_port = "relay", "127.0.0.1", 1, 18780
    assert sh.start(), sh.server.error
    try:
        assert sh.public_base() == "http://127.0.0.1:18780"
        qtbot.waitUntil(lambda: sh.relay is not None and sh.relay.state == "error", timeout=20000)
        assert "не отвечает" in sh.net_state or "Туннель" in sh.net_state
        assert (tmp_path / "home" / "relay_key").exists() and sh.relay_public_key().startswith("ssh-ed25519 ")
        acc = sh.server.access                                              # visitors through the VPS = localhost
        for _ in range(10):
            acc.failed("127.0.0.1")
        ban = acc.banned()[0]
        assert ban["key"] == "через VPS" and ban["until"] - ban["since"] <= 601
    finally:
        sh.stop()
        win.close()
    assert sh.relay is None


def test_relay_https_name_switches_to_https_and_localhost_tunnel(qtbot, tmp_path, monkeypatch):
    """With an https name (Caddy on the VPS) phones get https://<name>, and the tunnel opens on the VPS's localhost."""
    from musicplayer.server.relay import Relay, https_command, sslip_name
    assert sslip_name("203.0.113.10") == "203-0-113-10.sslip.io" and sslip_name("vps.example") == ""
    cmd = https_command("203-0-113-10.sslip.io", 8780)
    assert "reverse_proxy 127.0.0.1:8780" in cmd and "'203-0-113-10.sslip.io'" in cmd
    r = Relay(tmp_path, "203.0.113.10", "walle", 8780, 8780, bind="127.0.0.1")
    assert "127.0.0.1:8780:127.0.0.1:8780" in r.command(tmp_path / "k")
    win, sh, _ = make_sharing(qtbot, tmp_path, monkeypatch)
    sh.cfg.net_mode, sh.cfg.relay_host, sh.cfg.relay_ssh_port = "relay", "127.0.0.1", 1
    sh.cfg.relay_https = "203-0-113-10.sslip.io"
    assert sh.start(), sh.server.error
    try:
        assert sh.public_base() == "https://203-0-113-10.sslip.io" and sh.relay.bind == "127.0.0.1"
        assert sh.addresses()["internet"] == ["https://203-0-113-10.sslip.io"]
    finally:
        sh.stop()
        win.close()
