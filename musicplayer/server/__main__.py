"""Headless library server (without the desktop window):  python -m musicplayer.server [--port N] [--tunnel]

Uses the same catalogue and access key (remote.json) as the app; MUSICPLAYER_HOME picks another data folder.
"""

from __future__ import annotations

import argparse
import logging
import signal
import threading

from musicplayer.core.paths import data_dir
from musicplayer.server import config
from musicplayer.server.runner import RemoteServer, Tunnel, find_cloudflared


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m musicplayer.server", description="Walle Player library server")
    ap.add_argument("--port", type=int, help="port (default from remote.json, 8780)")
    ap.add_argument("--host", default="0.0.0.0", help="interface to listen on (default: all)")
    ap.add_argument("--tunnel", action="store_true", help="also open a Cloudflare quick tunnel (internet access)")
    ap.add_argument("--new-key", action="store_true", help="make a new owner key (old owner links stop working)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    home = data_dir()
    cfg = config.load(home)
    if args.new_key:
        cfg.owner["token"] = config.new_token()
        config.save(home, cfg)
    srv = RemoteServer(home / "library.db", home / "covers", cfg.users, args.port or cfg.port, args.host,
                       on_users=lambda: config.save(home, cfg), library_id=cfg.library_id)
    if not srv.start():
        print(f"Не удалось запустить сервер: {srv.error}", flush=True)
        return 1
    print("Walle Player: библиотека открыта для других устройств. Откройте на телефоне (или отсканируйте QR в приложении):", flush=True)
    for user in cfg.users:
        print(f"  {user['name']}:", flush=True)
        for u in srv.urls():
            print("    ", srv.pair_url(u, user), flush=True)
    tunnel = None
    if args.tunnel:
        tunnel = Tunnel(srv.port, find_cloudflared(home))
        if tunnel.start():
            for user in cfg.users:
                print(f"Из интернета ({user['name']}):", srv.pair_url(tunnel.url, user), flush=True)
        else:
            print("Туннель не открыт:", tunnel.error, flush=True)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    print("Ctrl+C - остановить", flush=True)
    while not stop.wait(0.5):
        pass
    if tunnel:
        tunnel.stop()
    srv.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
