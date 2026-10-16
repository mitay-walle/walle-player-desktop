"""Settings of the library server in ``remote.json`` next to the catalogue (shared by the app and the headless server).

Listeners: every person has an own key (own QR link), so likes and listening history do not mix.  The owner's likes
are the desktop app's «Избранное» and the owner's plays are the app's plays; a guest's likes and plays are kept
apart.  Labelling moods/genres (it teaches the mood model) is the owner's by default, ``can_label`` opens it to a guest.

Kept apart from settings.json on purpose: the desktop app rewrites settings.json from its own dataclass on exit, so a
key written there by a headless server could be lost and every phone would be locked out.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

DEFAULT_PORT = 8780
OWNER = "owner"


@dataclass
class RemoteConfig:
    enabled: bool = False           # the app starts the server at launch
    port: int = DEFAULT_PORT
    tunnel: bool = False            # also start the Cloudflare quick tunnel (internet access) at launch
    public_url: str = ""            # the address from outside (KeenDNS, own VPS, a tunnel service): goes into the QR
    allow_join: bool = True         # a new device may ask for access under a name; the owner answers in the app
    join_from_internet: bool = False  # ... also from outside the home network (off: from the home Wi-Fi only)
    library_id: str = ""            # stable id of this library: clients find it again when the address changes
    # internet access: off | static (provider's static IP, port forwarded on the router, UPnP) | relay (own VPS,
    # reverse SSH tunnel) | manual (an address set up elsewhere: KeenDNS, a tunnel service...)
    net_mode: str = ""
    static_host: str = ""           # empty = the router's internet IP (read over UPnP); or a DDNS name
    static_port: int = DEFAULT_PORT
    upnp: bool = True               # open the port on the router automatically
    relay_host: str = ""
    relay_user: str = "root"
    relay_ssh_port: int = 22
    relay_port: int = DEFAULT_PORT  # the port the VPS listens on for phones
    relay_https: str = ""           # https name served by Caddy on the VPS (e.g. 203-0-113-10.sslip.io): then the
    #                                 tunnel opens on the VPS's localhost only and phones use https://<name>
    users: list[dict] = field(default_factory=list)   # {id, name, token, owner, can_label, created}

    @property
    def owner(self) -> dict:
        return next(u for u in self.users if u.get("owner"))

    def user(self, uid: str) -> dict | None:
        return next((u for u in self.users if u["id"] == uid), None)


def new_token() -> str:
    return secrets.token_urlsafe(18)


def make_user(name: str, owner: bool = False, can_label: bool = False) -> dict:
    return {"id": OWNER if owner else "u" + secrets.token_hex(4), "name": name.strip() or "Слушатель",
            "token": new_token(), "owner": owner, "can_label": owner or can_label, "created": time.time()}


def add_user(cfg: RemoteConfig, name: str, can_label: bool = False) -> dict:
    u = make_user(name, can_label=can_label)
    cfg.users.append(u)
    return u


def remove_user(cfg: RemoteConfig, uid: str) -> None:
    cfg.users = [u for u in cfg.users if u["id"] != uid or u.get("owner")]


def _read(f: Path, tries: int = 3) -> dict | None:
    """The settings file, or None when it is missing/unreadable (a busy file is retried: antivirus, a parallel write)."""
    for k in range(tries):
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else None
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            if k + 1 < tries:
                time.sleep(0.3)
    return None


def load(home: Path) -> RemoteConfig:
    """The listeners' keys live here: an unreadable file is never silently replaced (that would lock every phone out) -
    the backup is used, and a broken file is kept aside before a new one is written."""
    f = Path(home) / "remote.json"
    raw = _read(f)
    if raw is None and f.exists():
        raw = _read(f.with_name("remote.json.bak"), 1)
        if raw is None:
            f.replace(f.with_name(f"remote.json.broken-{time.strftime('%Y%m%d-%H%M%S')}"))
    raw = raw or {}
    cfg = RemoteConfig(**{k: raw[k] for k in (x.name for x in fields(RemoteConfig)) if k in raw})
    cfg.users = [u for u in cfg.users if isinstance(u, dict) and u.get("id") and u.get("token")]
    changed = not f.exists()
    if not cfg.net_mode:
        cfg.net_mode = "manual" if cfg.public_url else "off"
    if not cfg.library_id:
        cfg.library_id = secrets.token_hex(8)
        changed = True
    if not any(u.get("owner") for u in cfg.users):
        owner = make_user("Я", owner=True)
        if raw.get("token"):                        # the first version had one key: it stays the owner's
            owner["token"] = raw["token"]
        cfg.users.insert(0, owner)
        changed = True
    if changed:
        save(home, cfg)
    return cfg


def save(home: Path, cfg: RemoteConfig) -> None:
    """Atomic: written to a temporary file and swapped in, the previous good file kept as remote.json.bak."""
    f = Path(home) / "remote.json"
    tmp = f.with_name("remote.json.tmp")
    tmp.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=1), encoding="utf-8")
    if f.exists():
        try:
            f.replace(f.with_name("remote.json.bak"))
        except OSError:
            pass
    tmp.replace(f)
