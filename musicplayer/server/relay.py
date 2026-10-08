"""Internet access through your own VPS: a reverse SSH tunnel kept up by the app.  Qt-free.

The computer connects OUT to the VPS (works behind any router / grey IP) and asks it to listen on a port:
``ssh -R 0.0.0.0:<remote>:127.0.0.1:<local> user@vps``; a phone opens ``http://<vps>:<remote>`` and the VPS passes the
connection down the tunnel.  Windows' own OpenSSH client is used; the key lives in the data folder and its public part
goes once into the VPS's ~/.ssh/authorized_keys.  One-time VPS setting: ``GatewayPorts clientspecified`` in sshd_config
(otherwise the VPS listens on its localhost only).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
def https_command(name: str, port: int) -> str:
    """One command for the VPS (root): Caddy with an automatic certificate for ``name`` in front of the tunnel.
    A free name for the server's IP without registering anything: <ip with dashes>.sslip.io."""
    return ("export DEBIAN_FRONTEND=noninteractive; apt-get install -y -qq caddy >/dev/null && "
            f"printf '%s {{\\n\\treverse_proxy 127.0.0.1:{port} {{\\n\\t\\tflush_interval -1\\n\\t}}\\n}}\\n' "
            f"'{name}' > /etc/caddy/Caddyfile && systemctl reload caddy && "
            "(command -v ufw >/dev/null && ufw allow 80/tcp && ufw allow 443/tcp; true) && echo ГОТОВО")


def sslip_name(host: str) -> str:
    """203.0.113.10 -> 203-0-113-10.sslip.io (resolves to that IP by itself)."""
    import ipaddress
    try:
        ip = ipaddress.ip_address(host.strip())
    except ValueError:
        return ""
    return (str(ip).replace(".", "-") if ip.version == 4 else str(ip).replace(":", "-")) + ".sslip.io"


SETUP_COMMAND = ("grep -q '^GatewayPorts' /etc/ssh/sshd_config && sed -i 's/^GatewayPorts.*/GatewayPorts clientspecified/' "
                 "/etc/ssh/sshd_config || echo 'GatewayPorts clientspecified' >> /etc/ssh/sshd_config; "
                 "systemctl restart ssh 2>/dev/null || systemctl restart sshd")


_job = None


def _bind_to_app(proc: subprocess.Popen) -> None:
    """Windows: put ssh into a job that is killed when the app's process ends - even when the app is killed hard,
    the tunnel does not stay behind holding the VPS port (the next start could not open it)."""
    global _job
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if _job is None:
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            job = k32.CreateJobObjectW(None, None)

            class BASIC(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class IO(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

            class EXTENDED(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                            ("PeakJobMemoryUsed", ctypes.c_size_t)]
            info = EXTENDED()
            info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # Extended info
                return
            _job = job
        k32.AssignProcessToJobObject(wintypes.HANDLE(_job), wintypes.HANDLE(int(proc._handle)))
    except Exception:  # noqa: BLE001 - without it the stale-pid cleanup below still helps
        log.debug("ssh not bound to a job", exc_info=True)


def _kill_stale(pidfile: Path) -> None:
    """A tunnel left by a previous run (the app was killed before the job existed): end it if it is still ssh."""
    try:
        pid = int(pidfile.read_text().strip())
    except (OSError, ValueError):
        return
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                             creationflags=NO_WINDOW).stdout.lower()
        if "ssh.exe" in out:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=NO_WINDOW)
            log.info("ended a stale tunnel (pid %s)", pid)
    pidfile.unlink(missing_ok=True)


def find_ssh(name: str = "ssh") -> str | None:
    exe = name + (".exe" if os.name == "nt" else "")
    builtin = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "OpenSSH" / exe
    return str(builtin) if builtin.is_file() else shutil.which(name)


def ensure_key(home: Path) -> tuple[Path, str]:
    """The tunnel's private key in the data folder (made once) and its public line for authorized_keys."""
    key = Path(home) / "relay_key"
    if not key.exists():
        keygen = find_ssh("ssh-keygen")
        if keygen is None:
            raise RuntimeError("нет ssh-keygen (OpenSSH-клиент Windows)")
        subprocess.run([keygen, "-q", "-t", "ed25519", "-N", "", "-C", "walle-player", "-f", str(key)], check=True,
                       capture_output=True, timeout=30, creationflags=NO_WINDOW)
    return key, Path(str(key) + ".pub").read_text(encoding="utf-8").strip()


class Relay:
    """Keeps the reverse tunnel up: reconnects after a drop with a growing pause; ``state`` / ``error`` for the UI."""

    def __init__(self, home: Path, host: str, user: str, local_port: int, remote_port: int, ssh_port: int = 22,
                 on_change=None, bind: str = "0.0.0.0") -> None:
        """``bind``: where the VPS listens - 0.0.0.0 (phones connect to it directly, http) or 127.0.0.1 (only Caddy
        on the same server reaches it and serves phones over https)."""
        self.bind = bind
        self.home, self.host, self.user = Path(home), host.strip(), (user or "root").strip()
        self.local_port, self.remote_port, self.ssh_port = int(local_port), int(remote_port), int(ssh_port)
        self.on_change = on_change or (lambda: None)
        self.state = "stopped"               # stopped | connecting | connected | error
        self.error: str | None = None
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.remote_port}"

    def command(self, key: Path) -> list[str]:
        return [find_ssh() or "ssh", "-N", "-T", "-i", str(key), "-p", str(self.ssh_port),
                "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=20",
                "-o", "ServerAliveCountMax=3", "-o", "StrictHostKeyChecking=accept-new",
                "-o", f"UserKnownHostsFile={self.home / 'relay_known_hosts'}", "-o", "ConnectTimeout=15",
                "-R", f"{self.bind}:{self.remote_port}:127.0.0.1:{self.local_port}", f"{self.user}@{self.host}"]

    def _set(self, state: str, error: str | None = None) -> None:
        if (state, error) != (self.state, self.error):
            self.state, self.error = state, error
            try:
                self.on_change()
            except Exception:  # noqa: BLE001
                log.exception("relay on_change failed")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="walle-relay", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        pause = 3.0
        _kill_stale(self.home / "relay.pid")
        while not self._stop.is_set():
            try:
                key, _ = ensure_key(self.home)
            except Exception as e:  # noqa: BLE001
                self._set("error", f"ключ SSH не создан: {e}")
                return
            self._set("connecting")
            started = time.time()
            try:
                self._proc = subprocess.Popen(self.command(key), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                              creationflags=NO_WINDOW)
            except OSError as e:
                self._set("error", f"ssh не запускается: {e}")
                return
            _bind_to_app(self._proc)
            try:
                (self.home / "relay.pid").write_text(str(self._proc.pid))
            except OSError:
                pass
            # no output = the forward is up: ssh -N stays silent unless something fails
            up = threading.Timer(4.0, lambda p=self._proc: self._set("connected") if p.poll() is None else None)
            up.start()
            err = (self._proc.stderr.read() or "").strip() if self._proc.stderr else ""
            self._proc.wait()
            up.cancel()
            if self._stop.is_set():
                break
            self._set("error", explain(err) or "соединение с сервером оборвалось")
            pause = 3.0 if time.time() - started > 60 else min(pause * 2, 120)   # a long session: retry soon
            self._stop.wait(pause)
        self._set("stopped")

    def stop(self) -> None:
        self._stop.set()
        p, self._proc = self._proc, None
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        if self._thread:
            self._thread.join(6)
        self._set("stopped")


def explain(stderr: str) -> str:
    """ssh's message in plain words."""
    s = stderr.lower()
    if "permission denied" in s:
        return "сервер не принял ключ: добавьте ключ Walle Player в ~/.ssh/authorized_keys на сервере"
    if "remote port forwarding failed" in s or "forwarding failed" in s:
        return "сервер не открыл порт: он занят или не включён GatewayPorts (выполните команду настройки на сервере)"
    if "could not resolve" in s:
        return "адрес сервера не найден (проверьте имя)"
    if "timed out" in s or "connection refused" in s or "no route" in s:
        return "сервер не отвечает по SSH (адрес, порт SSH, интернет)"
    if "host key verification failed" in s or "remote host identification has changed" in s:
        return "ключ сервера изменился — если сервер переустановлен, удалите relay_known_hosts в папке данных"
    return stderr.splitlines()[-1][:200] if stderr else ""
