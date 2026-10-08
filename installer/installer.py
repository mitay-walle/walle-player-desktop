"""Walle Player installer: one exe with the app inside (app.zip next to this script when built with PyInstaller).

Installs for the current user (no admin rights): files to %LOCALAPPDATA%\\Programs\\WallePlayer (or a chosen folder),
Start-menu and optional desktop shortcuts, an entry in Windows "Installed apps" with an uninstaller.  The user's
library, labels and models (%LOCALAPPDATA%\\WallePlayer) are never touched by install or uninstall.
"""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QProgressBar, QPushButton, QVBoxLayout, QWidget)

APP = "Walle Player"
EXE = "WallePlayer.exe"
VERSION = "0.2.0"
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
PAYLOAD = HERE / "app.zip"
DEFAULT_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / "WallePlayer"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\WallePlayer"

QSS = """
QWidget { background:#111113; color:#E6E3DA; font-family:'Segoe UI'; font-size:10pt; }
QLineEdit { background:#222226; border:1px solid #2c2c31; border-radius:8px; padding:6px 8px; }
QPushButton { background:#222226; border:none; border-radius:8px; padding:8px 16px; }
QPushButton:hover { background:#2a2a2f; }
QPushButton#primary { background:#D5A52C; color:#111113; font-weight:600; }
QPushButton#primary:hover { background:#E0B33F; }
QPushButton:disabled { color:#5F5F63; }
QCheckBox::indicator { width:16px; height:16px; border-radius:4px; border:1px solid #2c2c31; background:#222226; }
QCheckBox::indicator:checked { background:#D5A52C; border:1px solid #D5A52C; }
QProgressBar { background:#222226; border:none; border-radius:4px; height:8px; text-align:center; color:transparent; }
QProgressBar::chunk { background:#D5A52C; border-radius:4px; }
QLabel#muted { color:#92928F; }
"""


def ps(command: str) -> None:
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                   creationflags=0x08000000, check=False)        # CREATE_NO_WINDOW


def shortcut(lnk: Path, target: Path) -> None:
    lnk.parent.mkdir(parents=True, exist_ok=True)
    t = str(target).replace("'", "''")
    ps(f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{str(lnk).replace(chr(39), chr(39) * 2)}');"
       f"$s.TargetPath='{t}';$s.WorkingDirectory='{str(target.parent).replace(chr(39), chr(39) * 2)}';"
       f"$s.IconLocation='{t},0';$s.Save()")


def app_running() -> bool:
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {EXE}"], capture_output=True, text=True,
                         creationflags=0x08000000).stdout
    return EXE.lower() in out.lower()


def register_uninstall(target: Path, size_kb: int) -> None:
    import winreg
    script = target / "uninstall.ps1"
    start = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP}.lnk"
    desk = Path(os.environ.get("USERPROFILE", "")) / "Desktop" / f"{APP}.lnk"
    script.write_text(
        "# Walle Player uninstaller: removes the program and its shortcuts; your library data stays.\n"
        f"Get-Process WallePlayer -ErrorAction SilentlyContinue | Stop-Process -Force\n"
        f"Start-Sleep -Milliseconds 800\n"
        f"Remove-Item -LiteralPath '{start}' -ErrorAction SilentlyContinue\n"
        f"Remove-Item -LiteralPath '{desk}' -ErrorAction SilentlyContinue\n"
        f"Remove-Item -LiteralPath 'HKCU:\\{UNINSTALL_KEY}' -Recurse -ErrorAction SilentlyContinue\n"
        f"Set-Location $env:TEMP\n"
        f"Remove-Item -LiteralPath '{target}' -Recurse -Force -ErrorAction SilentlyContinue\n",
        encoding="utf-8-sig")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP)
        winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, VERSION)
        winreg.SetValueEx(k, "Publisher", 0, winreg.REG_SZ, "Walle Player")
        winreg.SetValueEx(k, "DisplayIcon", 0, winreg.REG_SZ, str(target / EXE))
        winreg.SetValueEx(k, "InstallLocation", 0, winreg.REG_SZ, str(target))
        winreg.SetValueEx(k, "EstimatedSize", 0, winreg.REG_DWORD, size_kb)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "UninstallString", 0, winreg.REG_SZ,
                          f'powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{script}"')


class Installer(QThread):
    progress = Signal(int, int)
    done = Signal(str)                 # "" = ok, else the error

    def __init__(self, target: Path, desktop: bool) -> None:
        super().__init__()
        self.target, self.desktop = target, desktop

    def run(self) -> None:
        try:
            with zipfile.ZipFile(PAYLOAD) as z:
                members = z.infolist()
                total = sum(m.file_size for m in members) or 1
                done = 0
                old = self.target / "_internal"
                if old.exists():                 # an update: replace the program files, nothing else
                    import shutil
                    shutil.rmtree(old, ignore_errors=True)
                for m in members:
                    z.extract(m, self.target)
                    done += m.file_size
                    self.progress.emit(done, total)
            exe = self.target / EXE
            start = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP}.lnk"
            shortcut(start, exe)
            if self.desktop:
                shortcut(Path(os.environ.get("USERPROFILE", "")) / "Desktop" / f"{APP}.lnk", exe)
            register_uninstall(self.target, total // 1024)
            self.done.emit("")
        except Exception as e:                   # noqa: BLE001 - shown to the user
            self.done.emit(f"{type(e).__name__}: {e}")


class Window(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Установка {APP} {VERSION}")
        icon = HERE / "icon.ico"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.setFixedWidth(560)
        logo = QLabel()
        wm = HERE / "wordmark.png"
        if wm.exists():
            logo.setPixmap(QPixmap(str(wm)).scaledToHeight(48, Qt.TransformationMode.SmoothTransformation))
        intro = QLabel("Музыкальный плеер вашей локальной библиотеки: «Моя волна», настроения, разметка, MCP.\n"
                       "Устанавливается только для текущего пользователя, права администратора не нужны.")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        self.path = QLineEdit(str(DEFAULT_DIR))
        browse = QPushButton("Обзор…")
        browse.clicked.connect(self.browse)
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        self.desktop = QCheckBox("Ярлык на рабочем столе")
        self.desktop.setChecked(True)
        self.launch = QCheckBox("Запустить после установки")
        self.launch.setChecked(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.hide()
        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.btn = QPushButton("Установить")
        self.btn.setObjectName("primary")
        self.btn.clicked.connect(self.install)
        note = QLabel("Ваша медиатека, метки и модели хранятся отдельно (%LOCALAPPDATA%\\WallePlayer) и при удалении "
                      "программы не стираются. Удаление — «Параметры → Приложения → Установленные приложения».")
        note.setObjectName("muted")
        note.setWordWrap(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 22)
        lay.setSpacing(12)
        lay.addWidget(logo)
        lay.addWidget(intro)
        lay.addWidget(QLabel("Папка установки"))
        lay.addLayout(row)
        lay.addWidget(self.desktop)
        lay.addWidget(self.launch)
        lay.addWidget(self.bar)
        lay.addWidget(self.status)
        lay.addWidget(note)
        lay.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignRight)

    def browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Папка установки", self.path.text())
        if d:
            self.path.setText(str(Path(d) / "WallePlayer") if Path(d).name.lower() != "walleplayer" else d)

    def install(self) -> None:
        if getattr(self, "_finished", False):
            if self.launch.isChecked():
                exe = Path(self.path.text()) / EXE
                subprocess.Popen([str(exe)], cwd=str(exe.parent), creationflags=0x00000008)   # DETACHED_PROCESS
            self.close()
            return
        if not PAYLOAD.exists():
            QMessageBox.critical(self, APP, "В установщике нет файлов программы (app.zip).")
            return
        while app_running():
            if QMessageBox.question(self, APP, "Walle Player сейчас запущен. Закройте его и нажмите «Да», чтобы продолжить.",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel) != \
                    QMessageBox.StandardButton.Yes:
                return
        target = Path(self.path.text()).expanduser()
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, APP, f"Не удалось создать папку:\n{e}")
            return
        for w in (self.path, self.desktop, self.btn):
            w.setEnabled(False)
        self.bar.show()
        self.status.setText("Копирую файлы…")
        self.worker = Installer(target, self.desktop.isChecked())
        self.worker.progress.connect(lambda d, t: self.bar.setValue(int(1000 * d / t)))
        self.worker.done.connect(self.finished)
        self.worker.start()

    def finished(self, error: str) -> None:
        if error:
            self.status.setText("Ошибка установки")
            QMessageBox.critical(self, APP, f"Установка не удалась:\n{error}")
            for w in (self.path, self.desktop, self.btn):
                w.setEnabled(True)
            return
        self._finished = True
        self.bar.setValue(1000)
        self.status.setText("Готово. Walle Player есть в меню Пуск" + (" и на рабочем столе." if self.desktop.isChecked() else "."))
        self.btn.setText("Запустить и закрыть" if self.launch.isChecked() else "Закрыть")
        self.btn.setEnabled(True)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    w = Window()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
