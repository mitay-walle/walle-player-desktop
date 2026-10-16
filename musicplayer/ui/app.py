"""Entry point: python -m musicplayer."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from musicplayer import __version__
from musicplayer.ui.context import AppContext
from musicplayer.ui.main_window import MainWindow
from musicplayer.ui.theme import apply_theme


def install_crash_handlers(win) -> None:
    """Uncaught exceptions are logged and shown in the status bar instead of silently killing a slot/thread."""
    log = logging.getLogger("musicplayer.crash")

    def hook(kind, value, tb):
        log.error("uncaught exception", exc_info=(kind, value, tb))
        try:
            win.statusBar().showMessage(f"Ошибка: {value} (подробности в app.log)", 10000)
        except Exception:
            pass

    sys.excepthook = hook
    import threading
    threading.excepthook = lambda a: hook(a.exc_type, a.exc_value, a.exc_traceback)


INSTANCE_KEY = "WallePlayer-single-instance"


def claim_single_instance(key: str = INSTANCE_KEY, message: bytes = b"show"):
    """Only one Walle Player per user: a second start asks the running one to show its window and quits.

    Returns the QLocalServer that keeps the claim (None when another instance answered)."""
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer(key)
    if sock.waitForConnected(300):
        sock.write(message)
        sock.waitForBytesWritten(300)
        sock.waitForDisconnected(1500)               # the running app hangs up once it has read the message
        sock.abort()
        return None
    QLocalServer.removeServer(key)                   # a stale name left by a crashed instance
    server = QLocalServer()
    server.listen(key)
    return server


def serve_instance(server, on_show, on_quit) -> None:
    """What a second start asks for: b"show" (a normal start) or b"quit" (``WallePlayer.exe --quit``)."""
    live: list = []                                  # the sockets stay referenced until their message is read

    def accept() -> None:
        while (sock := server.nextPendingConnection()) is not None:
            live.append(sock)
            done = []

            def read(sock=sock, done=done) -> None:
                msg = bytes(sock.readAll().data()).strip()
                if done or not msg and sock.state() == sock.LocalSocketState.ConnectedState:
                    return                           # nothing yet: readyRead / disconnected comes later
                done.append(True)
                if sock in live:
                    live.remove(sock)
                sock.disconnectFromServer()
                (on_quit if msg == b"quit" else on_show)()
            sock.readyRead.connect(read)
            sock.disconnected.connect(read)
            if sock.bytesAvailable() or sock.state() != sock.LocalSocketState.ConnectedState:
                read()
    server.newConnection.connect(accept)


def quit_running(key: str = INSTANCE_KEY, timeout: float = 60.0) -> int:
    """``WallePlayer.exe --quit``: the running app closes the normal way (session saved, catalogue closed, server and
    tunnel stopped) - for updates, instead of killing it.  0 = it has quit, 1 = none was running, 2 = still running."""
    import time
    from PySide6.QtNetwork import QLocalSocket
    if claim_single_instance(key, b"quit") is not None:
        return 1                                     # nobody answered (and the claim we took ends with this process)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        probe = QLocalSocket()
        probe.connectToServer(key)
        if not probe.waitForConnected(300):
            return 0
        probe.abort()
        time.sleep(0.3)
    return 2


def bring_to_front(win) -> None:
    if getattr(win, "mini", None) is not None and win.mini.isVisible():
        win.leave_mini()
    win.showNormal() if win.isMinimized() else win.show()
    win.raise_()
    win.activateWindow()


def create_app(home: Path | None = None, mcp: bool = True, port: int | None = None):
    """Build QApplication (reused if it exists), context, window and the MCP server thread."""
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Walle Player")
    app.setApplicationVersion(__version__)
    apply_theme(app)
    ctx = AppContext(home)
    win = MainWindow(ctx)
    win.mcp = None
    install_crash_handlers(win)
    if ctx.db_notice:
        win.statusBar().showMessage(ctx.db_notice, 30000)
    if mcp:
        try:
            from musicplayer.ui.mcp_server import McpServerThread
            win.mcp = McpServerThread(win, port or ctx.settings.mcp_port)
            if not win.mcp.start():
                win.statusBar().showMessage(f"MCP-сервер не запущен: {win.mcp.error}", 15000)
        except Exception as e:  # the player must work even if the MCP interface cannot start
            logging.getLogger(__name__).exception("MCP server unavailable")
            win.statusBar().showMessage(f"MCP-сервер недоступен: {e}", 15000)
            win.mcp = None
    return app, ctx, win


def redirected_data_dir() -> Path | None:
    """The package folder that silently receives this process's writes to %LOCALAPPDATA%, or None.

    Started by a packaged (MSIX) app - e.g. the Claude desktop app's terminal - a program inherits its file
    virtualization: what it writes to %LOCALAPPDATA%\\WallePlayer lands in Packages\\<app>\\LocalCache\\Local\\WallePlayer,
    while the same program started from the Start menu uses the real folder.  The two then drift apart (another
    catalogue, other listeners, the VPS settings «lost» after a restart).  A probe file shows where writes land."""
    if os.name != "nt" or os.environ.get("MUSICPLAYER_HOME"):
        return None
    base = Path(os.environ.get("LOCALAPPDATA", ""))
    folder = base / "WallePlayer"
    probe = folder / f".where-{os.getpid()}"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe.write_text("", encoding="utf-8")
        hits = list((base / "Packages").glob(f"*/LocalCache/Local/WallePlayer/{probe.name}"))
        probe.unlink(missing_ok=True)
    except OSError:
        return None
    return hits[0].parent if hits else None


def relaunch_outside_package() -> bool:
    """Frozen exe started inside a package's virtualization: start it again through the Windows shell (Explorer starts
    it, outside any package) with the same arguments and let this copy end.  False when nothing was done."""
    if not getattr(sys, "frozen", False) or "--no-relaunch" in sys.argv or redirected_data_dir() is None:
        return False
    import subprocess
    args = " ".join(f'"{a}"' if " " in a else a for a in [*sys.argv[1:], "--no-relaunch"])
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        "(New-Object -ComObject Shell.Application).ShellExecute($args[0], $args[1], $args[2])",
                        sys.executable, args, str(Path(sys.executable).parent)],
                       check=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except Exception:  # noqa: BLE001 - better a redirected app than none
        return False


def main() -> int:
    import multiprocessing
    multiprocessing.freeze_support()  # analysis uses a process pool; required in the frozen exe
    if sys.stdout is None:  # pythonw
        sys.stdout = sys.stderr = open(os.devnull, "w")
    if "--quit" not in sys.argv and relaunch_outside_package():
        return 0                                     # the copy started by the shell uses the real data folder
    from logging.handlers import RotatingFileHandler

    from musicplayer.core.paths import data_dir
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        handlers=[RotatingFileHandler(data_dir() / "app.log", maxBytes=2_000_000, backupCount=2,
                                                      encoding="utf-8"), logging.StreamHandler()])
    os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")
    if not getattr(sys, "frozen", False):
        # run from python.exe: own taskbar identity so Windows shows our icon, not python's.  The built exe must NOT
        # set it: then Windows matches the window to the Start-menu shortcut and takes the icon from there.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WallePlayer.App")
        except Exception:
            pass
    QApplication.instance() or QApplication(sys.argv)
    if "--quit" in sys.argv:
        return quit_running()
    server = claim_single_instance()
    if server is None:                               # already running: it has been brought to the front
        return 0
    app, ctx, win = create_app()
    app.setWindowIcon(win.windowIcon())
    from musicplayer.ui.tray import Tray
    win.tray = Tray(win)
    serve_instance(server, lambda: bring_to_front(win), win.tray.quit)
    app.setQuitOnLastWindowClosed(False)            # dialogs closing while the window is in the tray must not quit
    if "--tray" in sys.argv and win.tray.available:  # started with Windows: the server runs, the window waits
        win.tray.hid_window()
    else:
        win.show()
    code = app.exec()
    if win.mcp:
        win.mcp.stop()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
