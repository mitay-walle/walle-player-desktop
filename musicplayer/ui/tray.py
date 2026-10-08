"""Walle Player in the system tray: the library server keeps serving phones while the window is closed.

While the library is open for other devices, closing the window hides it to the tray (the server and the music go on);
«Выход» in the tray menu quits.  Access requests from new devices pop up from the tray.  «Запускать вместе с Windows»
puts a shortcut with ``--tray`` into the user's Startup folder: the app starts hidden, with the server running.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

log = logging.getLogger(__name__)
STARTUP_NAME = "Walle Player.lnk"


def startup_shortcut() -> Path | None:
    if os.name != "nt":
        return None
    return Path(os.environ.get("APPDATA", Path.home())) / "Microsoft/Windows/Start Menu/Programs/Startup" / STARTUP_NAME


def autostart_supported() -> bool:
    """Only the built exe: a shortcut to python.exe would start a console instead of the app."""
    return startup_shortcut() is not None and bool(getattr(sys, "frozen", False))


def autostart_enabled() -> bool:
    p = startup_shortcut()
    return bool(p and p.exists())


def set_autostart(on: bool) -> bool:
    """Adds / removes the Startup shortcut (``WallePlayer.exe --tray``).  True when the state is as asked."""
    p = startup_shortcut()
    if p is None:
        return False
    if not on:
        p.unlink(missing_ok=True)
        return True
    if not autostart_supported():
        return False
    exe = sys.executable
    q = lambda x: "'" + str(x).replace("'", "''") + "'"   # noqa: E731 - a PowerShell string literal
    # -Command does not hand extra arguments to the script ($args stays empty): the paths go into the text itself
    ps = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({q(p)}); $s.TargetPath = {q(exe)}; "
          f"$s.Arguments = '--tray'; $s.WorkingDirectory = {q(Path(exe).parent)}; $s.IconLocation = {q(exe)}; "
          "$s.Description = 'Walle Player'; $s.Save()")
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       check=True, timeout=30, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        log.exception("startup shortcut not created")
    return p.exists()


class Tray(QObject):
    """The tray icon and its menu; ``win`` is the main window (it owns the player and the sharing)."""

    def __init__(self, win) -> None:
        super().__init__(win)
        self.win = win
        self.icon = QSystemTrayIcon(win.windowIcon(), win)
        self.icon.setToolTip("Walle Player")
        menu = QMenu()
        self.a_open = menu.addAction("Открыть Walle Player", self.show_window)
        self.a_share = menu.addAction("Другие устройства…", self._open_share)
        self.a_state = menu.addAction("")
        self.a_state.setEnabled(False)
        menu.addSeparator()
        self.a_play = menu.addAction("Играть / пауза", win.ctx.player.toggle)
        self.a_next = menu.addAction("Следующий трек", win.ctx.player.next)
        menu.addSeparator()
        menu.addAction("Выход", self.quit)
        self.menu = menu
        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self._activated)
        self.icon.messageClicked.connect(self.show_window)
        win.sharing.changed.connect(lambda: QTimer.singleShot(0, self.update_state))
        win.sharing.join_requested.connect(self._join_balloon)
        win.sharing.banned.connect(self._ban_balloon)
        self._timer = QTimer(self, interval=5000)
        self._timer.timeout.connect(self.update_state)
        self._timer.start()
        self.hidden_once = False
        self.update_state()
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.icon.show()

    @property
    def available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable() and self.icon.isVisible()

    def update_state(self) -> None:
        sh = self.win.sharing
        if sh.running:
            n = len(sh.server.clients())
            text = f"Открыто для устройств · подключено: {n}" if n else "Открыто для устройств"
        else:
            text = "Только на этом компьютере"
        self.a_state.setText(text)
        t = self.win.ctx.player.current if hasattr(self.win.ctx.player, "current") else None
        title = f"{t['artist']} — {t['title']}" if isinstance(t, dict) and t.get("title") else ""
        self.icon.setToolTip("Walle Player\n" + text + (f"\n♪ {title}" if title else ""))

    def _activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.show_window()

    def show_window(self) -> None:
        from musicplayer.ui.app import bring_to_front
        bring_to_front(self.win)

    def _open_share(self) -> None:
        self.show_window()
        self.win.open_share()

    def _join_balloon(self, req: dict) -> None:
        if self.available:
            self.icon.showMessage("Новое устройство просит доступ",
                                  f"{req['name']} ({req['device']}) — откройте, чтобы разрешить",
                                  QSystemTrayIcon.MessageIcon.Information, 15000)

    def _ban_balloon(self, ban: dict) -> None:
        if self.available:
            import time as _t
            self.icon.showMessage("Адрес заблокирован", f"{ban['key']} подбирал ключ — заблокирован до "
                                  f"{_t.strftime('%d.%m %H:%M', _t.localtime(ban['until']))}. Снять — в «Другие устройства».",
                                  QSystemTrayIcon.MessageIcon.Warning, 10000)

    def hid_window(self) -> None:
        """Called when closing the window only hid it: say once where the app went."""
        if not self.hidden_once and self.available:
            self.hidden_once = True
            self.icon.showMessage("Walle Player работает в трее",
                                  "Библиотека открыта для других устройств. Выйти — правой кнопкой по значку → «Выход».",
                                  QSystemTrayIcon.MessageIcon.Information, 6000)

    def quit(self) -> None:
        self.win.quitting = True
        self.win.close()
        self.icon.hide()
        QApplication.instance().quit()
