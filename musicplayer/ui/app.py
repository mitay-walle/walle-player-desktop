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


def claim_single_instance(key: str = INSTANCE_KEY):
    """Only one Walle Player per user: a second start asks the running one to show its window and quits.

    Returns the QLocalServer that keeps the claim (None when another instance answered)."""
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer(key)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return None
    QLocalServer.removeServer(key)                   # a stale name left by a crashed instance
    server = QLocalServer()
    server.listen(key)
    return server


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


def main() -> int:
    import multiprocessing
    multiprocessing.freeze_support()  # analysis uses a process pool; required in the frozen exe
    if sys.stdout is None:  # pythonw
        sys.stdout = sys.stderr = open(os.devnull, "w")
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
    server = claim_single_instance()
    if server is None:                               # already running: it has been brought to the front
        return 0
    app, ctx, win = create_app()
    server.newConnection.connect(lambda: (server.nextPendingConnection(), bring_to_front(win)))
    app.setWindowIcon(win.windowIcon())
    from musicplayer.ui.tray import Tray
    win.tray = Tray(win)
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
