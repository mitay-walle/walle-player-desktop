"""Running the library server: uvicorn on a thread, LAN addresses, discovery and the Cloudflare tunnel.  Qt-free.

- LAN: the server listens on every interface (0.0.0.0); phones open ``http://<computer's LAN address>:<port>``.
- Discovery: a UDP responder on the same port number answers ``WALLE_DISCOVER`` with JSON (name, port, version),
  so the mobile app can find the computer on the network without typing an address (it still needs the key).
- Internet: ``cloudflared tunnel --url`` gives a temporary https://*.trycloudflare.com address without an account or
  router setup (the address changes on every start).  cloudflared.exe is looked up in the data folder,
  third_party/cloudflared and PATH.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from musicplayer import __version__
from musicplayer.server.api import Access, Library, build_app

log = logging.getLogger(__name__)
DISCOVER = b"WALLE_DISCOVER"


VIRTUAL_NAMES = ("vpn", "tun", "tap", "radmin", "hamachi", "zerotier", "tailscale", "wireguard", "outline", "vethernet",
                 "virtualbox", "vmware", "wsl", "hyper-v", "loopback", "docker", "browbox")
VIRTUAL_PREFIXES = ("127.", "169.254.", "0.", "26.", "25.")      # 26.x Radmin VPN, 25.x Hamachi
_lan_cache: tuple[float, list[str]] = (0.0, [])


def _physical_ipv4() -> list[str] | None:
    """IPv4 addresses of real network cards (Wi-Fi, Ethernet) on Windows; None when it cannot be asked."""
    if os.name != "nt":
        return None
    script = ("Get-NetAdapter | Where-Object { $_.Status -eq 'Up' } | ForEach-Object { $a = $_; "
              "Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | "
              "ForEach-Object { $_.IPAddress + '|' + $a.HardwareInterface + '|' + $a.Name + '|' + $a.InterfaceDescription } }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    found = []
    for line in out.splitlines():
        parts = line.strip().split("|")
        if len(parts) < 4:
            continue
        ip, hardware, names = parts[0], parts[1] == "True", (parts[2] + " " + parts[3]).lower()
        if hardware and not any(v in names for v in VIRTUAL_NAMES) and not ip.startswith(VIRTUAL_PREFIXES):
            found.append(ip)
    return found


def lan_addresses() -> list[str]:
    """IPv4 addresses of this computer that phones in the home network can reach, the most likely first.  VPN and
    virtual adapters (Radmin, Outline, WSL, Hyper-V...) are left out: a phone cannot open the library through them."""
    global _lan_cache
    if time.time() - _lan_cache[0] < 60:                 # asked often (the dialog refreshes): Windows is slow to answer
        return list(_lan_cache[1])
    primary = None
    try:                                    # the interface that routes outside (no packet is actually sent)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.255.255", 1))
            primary = s.getsockname()[0]
    except OSError:
        pass
    physical = _physical_ipv4()
    if physical is None:                     # not Windows / no answer: every address minus the known virtual ranges
        physical = []
        try:
            physical = [i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
        except OSError:
            pass
        physical = [a for a in [primary, *physical] if a and not a.startswith(VIRTUAL_PREFIXES)]
    out = list(dict.fromkeys(a for a in ([primary] if primary in physical else []) + physical))

    def rank(a: str) -> int:               # home networks first
        return 0 if a.startswith("192.168.") else 1 if a.startswith("10.") else 2
    out = sorted(out, key=rank)
    _lan_cache = (time.time(), out)
    return list(out)


def global_ipv6() -> list[str]:
    """This computer's internet IPv6 addresses (2000::/3) on real network cards, the stable one first (Windows also
    makes temporary «privacy» addresses that change every day - a link must not use those).  Empty without IPv6."""
    if os.name != "nt":
        return []
    script = ("Get-NetAdapter | Where-Object { $_.Status -eq 'Up' -and $_.HardwareInterface } | ForEach-Object { $a = $_; "
              "Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv6 -ErrorAction SilentlyContinue | "
              "Where-Object { $_.AddressState -eq 'Preferred' } | ForEach-Object { $_.IPAddress + '|' + "
              "[int]$_.ValidLifetime.TotalSeconds + '|' + $a.Name + ' ' + $a.InterfaceDescription } }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=15,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        parts = line.strip().split("|")
        if len(parts) < 3 or not re.match(r"^[23][0-9a-f]{3}:", parts[0], re.I):
            continue
        if any(v in parts[2].lower() for v in VIRTUAL_NAMES):
            continue
        try:
            life = int(parts[1])
        except ValueError:
            life = 0
        found.append((life, parts[0]))
    return [ip for _, ip in sorted(found, reverse=True)]       # the longest-lived (stable) address first


def listen_sockets(port: int, host: str = "0.0.0.0") -> list[socket.socket]:
    """Listening sockets for the server: IPv4 and, when the computer has IPv6, IPv6 too (phones from the internet
    reach a home computer over IPv6 even when the provider gives a «grey» IPv4).  OSError when the port is busy."""
    socks = []
    s4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s4.bind((host if ":" not in host else "0.0.0.0", port))
        s4.listen(128)
    except OSError:
        s4.close()
        raise
    socks.append(s4)
    if host in ("0.0.0.0", "::") and socket.has_ipv6:
        s6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            s6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            s6.bind(("::", port))
            s6.listen(128)
            socks.append(s6)
        except OSError as e:                               # no IPv6 on this computer: IPv4 is enough
            log.info("no IPv6 listener: %s", e)
            s6.close()
    return socks


def port_free(port: int, host: str = "0.0.0.0") -> bool:
    """Can this port be listened on (no other program holds it)?"""
    with socket.socket() as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def network_problems(lan_ip: str | None, program: str | None = None) -> list[str]:
    """Why a phone on the Wi-Fi may not reach this computer (Windows only, read-only checks): the network marked as
    public, no inbound firewall permission for the program.  Each item is a ready instruction for the user."""
    if os.name != "nt" or not lan_ip:
        return []
    program = program or sys.executable
    script = (
        f"$p = Get-NetIPAddress -IPAddress '{lan_ip}' -ErrorAction SilentlyContinue | Get-NetConnectionProfile -ErrorAction SilentlyContinue;"
        "if ($p) { 'NET|' + $p.NetworkCategory + '|' + $p.Name };"
        f"Get-NetFirewallApplicationFilter -Program '{program}' -ErrorAction SilentlyContinue | Get-NetFirewallRule -ErrorAction SilentlyContinue |"
        " Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' } |"
        " ForEach-Object { 'FW|' + $_.Action + '|' + $_.Profile }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=30,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    category, name, allow, block = "", "", [], []
    for line in out.splitlines():
        parts = line.strip().split("|")
        if parts[0] == "NET" and len(parts) >= 3:
            category, name = parts[1], parts[2]
        elif parts[0] == "FW" and len(parts) >= 3:
            (allow if parts[1] == "Allow" else block).append(parts[2])
    problems = []
    if category == "Public":
        problems.append(f"Wi-Fi «{name}» отмечена в Windows как общественная — так Windows не пускает телефон. "
                        f"Параметры → Сеть и Интернет → Wi-Fi → «{name}» → Тип сетевого профиля: «Частная».")
    profile = "Private" if category in ("Private", "Public") else category or "Private"
    fits = lambda prof: "Any" in prof or profile in prof   # noqa: E731
    if any(fits(p) for p in block):
        problems.append("В брандмауэре Windows Walle Player запрещены входящие подключения. Брандмауэр Защитника Windows → "
                        "Разрешение взаимодействия с приложением → отметьте Walle Player для частных сетей.")
    elif not any(fits(p) for p in allow):
        problems.append("В брандмауэре Windows нет разрешения для Walle Player. Брандмауэр Защитника Windows → "
                        "Разрешение взаимодействия с приложением → «Разрешить другое приложение» → "
                        f"{program} → отметить «Частная».")
    return problems


class Discovery:
    """Answers UDP ``WALLE_DISCOVER`` broadcasts with this server's name and port."""

    def __init__(self, port: int, name: str, library_id: str = "") -> None:
        self.port, self.name, self.library_id = port, name, library_id
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            if os.name != "nt":   # on Windows SO_REUSEADDR lets us share a port another program holds: replies get lost
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", self.port))
            s.settimeout(0.5)
        except OSError as e:
            log.warning("discovery responder not started: %s", e)
            return
        self._sock = s
        self._thread = threading.Thread(target=self._loop, name="walle-discovery", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        reply = json.dumps({"app": "WallePlayer", "name": self.name, "port": self.port, "library": self.library_id,
                            "version": __version__}).encode()
        while self._sock is not None:
            try:
                data, addr = self._sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                break
            if data.strip() == DISCOVER:
                try:
                    self._sock.sendto(reply, addr)
                except OSError:
                    pass

    def stop(self) -> None:
        s, self._sock = self._sock, None
        if s is not None:
            s.close()
        if self._thread is not None:
            self._thread.join(1.0)


class RemoteServer:
    """The library server on a background thread; ``start()`` / ``stop()``."""

    def __init__(self, db_path: Path, covers: Path, users: list[dict], port: int, host: str = "0.0.0.0",
                 on_change: Callable[[list[int]], None] | None = None, name: str | None = None,
                 on_users: Callable[[], None] | None = None, on_join: Callable[[dict], None] | None = None,
                 library_id: str = "", on_ban: Callable[[dict], None] | None = None) -> None:
        """``users``: the listeners from remote.json ({id, name, token, owner, can_label}); the list is shared, so
        adding a listener or changing a key takes effect at once (``set_users`` replaces it)."""
        self.db_path, self.covers, self.port, self.host = Path(db_path), Path(covers), int(port), host
        self.name = name or socket.gethostname()
        self.library_id = library_id
        self.access = Access(users, on_users, on_join, ban_file=Path(db_path).parent / "bans.json", on_ban=on_ban)
        self.access.addresses = lambda: {"lan": self.urls(), "internet": []}   # the app adds its internet address
        self.on_change = on_change
        self.error: str | None = None
        self._server = None
        self._thread: threading.Thread | None = None
        self._lib: Library | None = None
        self._discovery: Discovery | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and self.error is None

    def set_users(self, users: list[dict]) -> None:
        """New listeners/keys: a device whose key is gone loses access at once."""
        self.access.users = users

    def urls(self) -> list[str]:
        return [f"http://{a}:{self.port}" for a in lan_addresses()] or [f"http://127.0.0.1:{self.port}"]

    @staticmethod
    def pair_url(base: str, user: dict) -> str:
        return f"{base.rstrip('/')}/?k={user['token']}"

    def clients(self) -> list[dict]:
        return self.access.connected()

    def start(self, wait: float = 5.0) -> bool:
        """Starts and waits until the port is listening; False (and ``error``) when it cannot."""
        import uvicorn
        self.error = None
        self.port_busy = False
        try:
            self._sockets = listen_sockets(self.port, self.host)
        except OSError:
            self.port_busy = True
            self.error = f"порт {self.port} занят другой программой (например, ещё одним сервером Walle Player)"
            return False
        try:
            self._lib = Library(self.db_path, self.covers, self.on_change)
            app = build_app(self._lib, self.access, {"name": self.name, "library": self.library_id})
            config = uvicorn.Config(app, host=self.host, port=self.port, log_level="warning", lifespan="off",
                                    log_config=None, access_log=False, proxy_headers=True,
                                    forwarded_allow_ips="127.0.0.1")
            self._server = uvicorn.Server(config)
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
            log.exception("library server could not be created")
            for s in self._sockets:
                s.close()
            return False

        def serve() -> None:
            try:
                asyncio.run(self._server.serve(sockets=self._sockets))
            except BaseException as e:  # noqa: BLE001 - uvicorn exits via SystemExit when the port is busy
                self.error = self.error or f"порт {self.port} занят или недоступен"
            if not self._server.started and self.error is None:
                self.error = f"порт {self.port} занят или недоступен"

        self._thread = threading.Thread(target=serve, name="walle-server", daemon=True)
        self._thread.start()
        deadline = time.time() + wait
        while time.time() < deadline and not self._server.started and self._thread.is_alive():
            time.sleep(0.05)
        if not self._server.started:
            self.error = self.error or f"порт {self.port} занят или недоступен"
            self.stop()
            return False
        self._discovery = Discovery(self.port, self.name, self.library_id)
        self._discovery.start()
        threading.Thread(target=lambda: log.info("library server on %s", ", ".join(self.urls())),   # warms the
                         name="walle-addresses", daemon=True).start()                            # address cache
        return True

    def stop(self, timeout: float = 3.0) -> None:
        if self._discovery is not None:
            self._discovery.stop()
            self._discovery = None
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None
        for s in getattr(self, "_sockets", []):
            try:
                s.close()
            except OSError:
                pass
        self._sockets = []
        if self._lib is not None:
            self._lib.close()
            self._lib = None


def find_cloudflared(home: Path | None = None) -> Path | None:
    exe = "cloudflared.exe" if os.name == "nt" else "cloudflared"
    roots = [Path(home)] if home else []
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    roots += [base / "third_party" / "cloudflared", Path(sys.executable).parent / "third_party" / "cloudflared"]
    for r in roots:
        if (r / exe).is_file():
            return r / exe
    found = shutil.which("cloudflared")
    return Path(found) if found else None


class Tunnel:
    """Cloudflare quick tunnel to the local server: a public https address, no account, no router setup."""

    URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

    def __init__(self, port: int, exe: Path | None) -> None:
        self.port, self.exe = port, exe
        self.url: str | None = None
        self.error: str | None = None
        self._proc: subprocess.Popen | None = None
        self._ready = threading.Event()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, wait: float = 30.0) -> bool:
        if self.exe is None:
            self.error = "cloudflared не найден"
            return False
        self.url, self.error = None, None
        self._ready.clear()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._proc = subprocess.Popen(
                [str(self.exe), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", creationflags=flags)
        except OSError as e:
            self.error = str(e)
            return False
        threading.Thread(target=self._read, name="walle-tunnel", daemon=True).start()
        self._ready.wait(wait)
        if self.url is None:
            self.error = self.error or "туннель не ответил (нет интернета?)"
        return self.url is not None

    def _read(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        for line in self._proc.stderr:
            m = self.URL_RE.search(line)
            if m and self.url is None:
                self.url = m.group(0)
                self._ready.set()
            elif " ERR " in line and self.url is None:
                self.error = line.strip()[-200:]
        self._ready.set()                    # the process ended

    def stop(self) -> None:
        p, self._proc = self._proc, None
        self.url = None
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
