"""The desktop app with its library on a home server: a synced copy, playback from the server, deleting there."""
import os
import shutil
import socket
import urllib.request

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from .conftest import make_mp3  # noqa: E402

KEY = "owner-key-0123456789abcdef"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def setup(qtbot, tmp_path, library, monkeypatch):
    """A server with the library (journaling) and a desktop app holding a copy of its catalogue."""
    from musicplayer.core import sync
    from musicplayer.core.db import Database
    from musicplayer.core.scanner import scan
    from musicplayer.server import config
    from musicplayer.server.runner import RemoteServer
    from musicplayer.ui.app import create_app
    srv_home, home = tmp_path / "srv", tmp_path / "home"
    srv_home.mkdir()
    home.mkdir()
    monkeypatch.setenv("MUSICPLAYER_HOME", str(home))                      # analysis jobs read the controls here
    db = Database(srv_home / "library.db")
    scan(db, library, srv_home / "covers")
    sync.enable(db.conn)
    db.close()
    shutil.copy(srv_home / "library.db", home / "library.db")             # the desktop's copy (as after the move)
    users = [{"id": "owner", "name": "Я", "token": KEY, "owner": True, "can_label": True}]
    cfg = config.RemoteConfig(users=users, library_id="lib-test")
    config.save(home, cfg)
    port = free_port()
    srv = RemoteServer(srv_home / "library.db", srv_home / "covers", users, port, host="127.0.0.1",
                       library_id="lib-test")
    assert srv.start(), srv.error
    _, ctx, win = create_app(home=home, mcp=False)
    qtbot.addWidget(win)
    ctx.settings.library_root = str(tmp_path / "mirror")                  # this computer has no copy of the music
    yield ctx, win, srv, srv_home, library, f"http://127.0.0.1:{port}"
    srv.stop()


def sync_and_wait(qtbot, ctx):
    before = ctx.library.last_sync
    ctx.library.sync_now()
    qtbot.waitUntil(lambda: ctx.library.last_sync > before, timeout=20000)
    assert ctx.library.error is None, ctx.library.error


def test_desktop_and_server_stay_in_sync(qtbot, setup):
    from musicplayer.core import likes
    from musicplayer.core.db import Database
    ctx, win, srv, srv_home, library, url = setup
    st = ctx.library.connect_to(url, str(library))
    assert st["enabled"] and st["node"]
    t = ctx.db.search("Yesterday")[0]["id"]
    likes.set_liked(ctx.db, "owner", "track", str(t), True)                # here
    sdb = Database(srv_home / "library.db")
    sdb.set_moods([t], ["Грустное"], "set")                                # on the server (a phone)
    sdb.close()
    sync_and_wait(qtbot, ctx)
    sdb = Database(srv_home / "library.db")
    assert likes.is_liked(sdb, "owner", "track", str(t))
    sdb.close()
    assert ctx.db.get_track(t)["moods"] == ["Грустное"]
    assert win.start_scan() is None                                        # the server scans its folder itself


def test_new_server_tracks_play_from_the_server_and_delete_there(qtbot, setup, monkeypatch):
    from musicplayer.core import trash
    from musicplayer.core.db import Database
    from musicplayer.core.scanner import scan
    from musicplayer.ui.player import source_of
    ctx, win, srv, srv_home, library, url = setup
    ctx.library.connect_to(url, str(library))
    make_mp3(library / "Новое" / "Песня.mp3", artist="Новое", title="Песня")
    sdb = Database(srv_home / "library.db")
    scan(sdb, library, srv_home / "covers")                               # the server's scan finds it
    sdb.close()
    sync_and_wait(qtbot, ctx)
    t = ctx.db.search("Песня")[0]
    assert t["path"] == os.path.join(ctx.settings.library_root, "Новое", "Песня.mp3")
    src = source_of(t)
    assert src.startswith(url + f"/api/stream/{t['id']}")                  # not here: the server's stream
    assert urllib.request.urlopen(src, timeout=5).status == 200
    gone = []
    monkeypatch.setattr(trash, "remove_file", lambda p, root=None: gone.append(p) or True)
    assert ctx.library.trash(t["id"])
    assert gone and gone[0].endswith("Песня.mp3")
    sync_and_wait(qtbot, ctx)
    assert not ctx.db.search("Песня")                                      # missing on the server -> here too


def test_a_guest_key_cannot_sync(setup):
    import json
    ctx, win, srv, srv_home, library, url = setup
    srv.set_users(srv.access.users + [{"id": "u1", "name": "Гость", "token": "guest-key-0123456789", "owner": False}]) \
        if hasattr(srv, "access") else None
    req = urllib.request.Request(url + "/api/sync/pull?since=0", headers={"Authorization": "Bearer guest-key-0123456789"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code in (401, 403)
    ok = urllib.request.Request(url + "/api/sync/pull?since=0&node=x", headers={"Authorization": f"Bearer {KEY}"})
    assert "entries" in json.loads(urllib.request.urlopen(ok, timeout=5).read())


def test_analysis_fetches_files_this_computer_does_not_have(qtbot, setup):
    """All analysis runs on the desktop: a track only the server has is downloaded for it, then deleted."""
    from musicplayer.core import remotefile
    from musicplayer.core.db import Database
    from musicplayer.core.scanner import scan
    ctx, win, srv, srv_home, library, url = setup
    ctx.library.connect_to(url, str(library))
    make_mp3(library / "Новое" / "Анализ.mp3", artist="Новое", title="Анализ")
    sdb = Database(srv_home / "library.db")
    scan(sdb, library, srv_home / "covers")
    sdb.close()
    sync_and_wait(qtbot, ctx)
    assert ctx.library.last_result["new"] == 1
    t = ctx.db.search("Анализ")[0]
    assert not os.path.exists(t["path"]) and remotefile.available()       # the client handed jobs the server
    with remotefile.local_copy(t["id"], t["path"]) as p:
        assert os.path.exists(p) and open(p, "rb").read() == (library / "Новое" / "Анализ.mp3").read_bytes()
    assert not os.path.exists(p)                                          # fetched for the analysis only


def test_the_music_folder_here_mirrors_the_server(qtbot, setup):
    """Full mirror: new files on the server are downloaded into the same folders here; nothing here is touched."""
    from musicplayer.core.db import Database
    from musicplayer.core.scanner import scan
    from musicplayer.ui.player import source_of
    ctx, win, srv, srv_home, library, url = setup
    mirror = os.path.join(ctx.settings.library_root)
    os.makedirs(os.path.join(mirror, "Своё"))
    mine = os.path.join(mirror, "Своё", "не трогать.mp3")
    open(mine, "wb").write(b"local only")
    ctx.library.connect_to(url, str(library))
    make_mp3(library / "Новое" / "Зеркало.mp3", artist="Новое", title="Зеркало")
    sdb = Database(srv_home / "library.db")
    scan(sdb, library, srv_home / "covers")
    sdb.close()
    sync_and_wait(qtbot, ctx)
    t = ctx.db.search("Зеркало")[0]
    assert t["path"] == os.path.join(mirror, "Новое", "Зеркало.mp3")
    assert open(t["path"], "rb").read() == (library / "Новое" / "Зеркало.mp3").read_bytes()
    assert source_of(t) == t["path"]                                       # plays from here now
    st = ctx.library.status()["mirror"]
    assert st["copied"] >= 1 and not os.path.exists(t["path"] + ".part")
    assert open(mine, "rb").read() == b"local only"                        # files only here stay as they are
    ctx.library.mirror["checked"] = 0
    sync_and_wait(qtbot, ctx)
    assert ctx.library.last_result["mirrored"] == 0                         # nothing to fetch twice


def test_the_user_controls_the_network_use(qtbot, setup, monkeypatch):
    """Pause stops the sync and every download at once; the speed limit holds; the «Сервер» menu sets them."""
    import io
    import time as _t
    from musicplayer.core import remotefile
    from musicplayer.ui import hideactions
    ctx, win, srv, srv_home, library, url = setup
    ctx.library.connect_to(url, str(library))
    sync_and_wait(qtbot, ctx)
    assert win.btn_sync.isVisibleTo(win) and "Сервер:" in win.btn_sync.text()
    ctx.library.set_limit(64)                                              # 64 KB/s
    t0 = _t.monotonic()
    remotefile.copy_limited(io.BytesIO(b"x" * 200_000), io.BytesIO(), chunk=16 << 10)
    assert _t.monotonic() - t0 >= 2.0                                      # ~3 s for 200 KB, not instant
    ctx.library.set_paused(True)
    with pytest.raises(remotefile.Paused):
        remotefile.copy_limited(io.BytesIO(b"x" * 10), io.BytesIO())
    before = ctx.library.last_sync
    ctx.library.sync_now()
    qtbot.wait(300)
    assert ctx.library.last_sync == before and "пауза" in win.btn_sync.text()   # nothing goes on paused
    seen = []

    def answer(menu, pos):
        acts = {a.text(): a for a in menu.actions()}
        seen.append(list(acts))
        acts["Пауза: не трогать сеть"].trigger()                            # un-pause from the menu
        speed = next(a for a in menu.actions() if a.menu())
        next(a for a in speed.menu().actions() if a.text() == "2 МБ/с").trigger()
    monkeypatch.setattr(hideactions, "show_menu", answer)
    win.sync_menu()
    assert seen and "Синхронизировать сейчас" in seen[0]
    assert not ctx.settings.sync_paused and ctx.settings.sync_limit_kbps == 2048
    qtbot.waitUntil(lambda: ctx.library.last_sync > before, timeout=20000)    # resumed by itself


def test_share_link_has_the_internet_address_and_no_key(qtbot, tmp_path):
    from types import SimpleNamespace
    from musicplayer.server import config
    from musicplayer.ui.libraryclient import share_link
    ctx = SimpleNamespace(home=tmp_path, settings=SimpleNamespace(library_server="auto"))
    config.save(tmp_path, config.RemoteConfig(users=[{"id": "owner", "token": KEY, "owner": True}]))
    assert share_link(ctx, "track/5") is None                             # nowhere to open it from outside
    config.save(tmp_path, config.RemoteConfig(users=[{"id": "owner", "token": KEY, "owner": True}], net_mode="relay",
                                              relay_host="203.0.113.10", relay_https="203-0-113-10.sslip.io"))
    url = share_link(ctx, "track/5")
    assert url == "https://203-0-113-10.sslip.io/open/track/5" and KEY not in url
    from musicplayer.ui.libraryclient import album_path, artist_path
    assert artist_path("AC/DC") == "artist/AC%2FDC" and album_path("A B", "X/Y") == "album/A%20B/X%2FY"
