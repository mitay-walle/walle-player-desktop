"""«Другие устройства» while this computer works with the library on a home server: the tab «Сервер» - the server's
listeners, their QR codes and links, add / rename / remove, labelling rights, a new key, likes import (all through the
server's owner-only /api/listeners; the listeners in this computer's remote.json are an old copy); the tab «Этот
компьютер» - the library server of this computer (ShareDialog)."""

from __future__ import annotations

import threading
from urllib.parse import quote

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QInputDialog, QLabel, QMessageBox,
                               QPushButton, QStackedWidget, QTabBar, QVBoxLayout, QWidget)

from musicplayer.server.runner import RemoteServer
from musicplayer.ui.icons import make_icon_button
from musicplayer.ui.share_dialog import Line, qr_pixmap
from musicplayer.ui.theme import TOKENS


def bases(state: dict) -> list[tuple[str, str]]:
    """[(label, base url)] of the server: the internet addresses first (they work everywhere), then the home ones."""
    a = state.get("addresses") or {}
    return ([("Из интернета", b) for b in a.get("internet") or [] if b]
            + [("Домашняя сеть", b) for b in a.get("lan") or [] if b])


class ServerListenersDialog(QDialog):
    def __init__(self, ctx, sharing=None, parent=None) -> None:
        super().__init__(parent)
        self.ctx, self.sharing = ctx, sharing
        self.state: dict = {}
        self.setWindowTitle("Другие устройства — библиотека на сервере")
        outer = QVBoxLayout(self)
        self.tabs = QTabBar()              # a bar + a stack (not QTabWidget): the window takes the shown tab's size
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        self.pages = QStackedWidget()
        outer.addWidget(self.tabs)
        outer.addWidget(self.pages)
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 6, 0, 0)
        self.tabs.addTab("Сервер")
        self.tabs.setTabToolTip(0, "Слушатели сервера библиотеки")
        self.pages.addWidget(page)
        self.local = None                 # «Этот компьютер»: the library server of this computer (the old dialog)
        if sharing is not None:
            from musicplayer.ui.share_dialog import ShareDialog
            self.local = ShareDialog(sharing, embedded=True)
            self.local.setWindowFlags(Qt.WindowType.Widget)
            self.local.layout().setContentsMargins(0, 6, 0, 0)
            self.tabs.addTab("Этот компьютер")
            self.tabs.setTabToolTip(1, "Сервер на этом компьютере: открыть библиотеку для устройств отсюда")
            self.pages.addWidget(self.local)
        self.tabs.setVisible(self.local is not None)
        body = QHBoxLayout()
        self.qr = QLabel()
        self.qr.setFixedSize(200, 200)
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr.setStyleSheet(f"background:{TOKENS['surface2']}; border-radius:12px;")
        body.addWidget(self.qr)
        right = QVBoxLayout()
        nrow = QHBoxLayout()
        self.user = QComboBox()
        self.user.setToolTip("Слушатель: свои лайки и история")
        nrow.addWidget(self.user, 1)
        self.btn_ren = QPushButton()
        make_icon_button(self.btn_ren, "edit", TOKENS["text"], 20, "Переименовать")
        self.btn_add = QPushButton()
        make_icon_button(self.btn_add, "plus", TOKENS["text"], 20, "Добавить слушателя: свой QR, свои лайки и история")
        self.btn_del = QPushButton()
        make_icon_button(self.btn_del, "trash", TOKENS["text"], 20, "Удалить слушателя")
        for b in (self.btn_ren, self.btn_add, self.btn_del):
            nrow.addWidget(b)
        right.addLayout(nrow)
        self.can_label = QCheckBox("Может размечать")
        self.can_label.setToolTip("Настроения и жанры: это учит вашу модель настроений")
        lrow = QHBoxLayout()
        lrow.addWidget(self.can_label, 1)
        self.btn_likes = QPushButton()
        make_icon_button(self.btn_likes, "import", TOKENS["text"], 20,
                         "Импорт лайков из файла: «Исполнитель — Название», CSV, JSON или M3U")
        lrow.addWidget(self.btn_likes)
        right.addLayout(lrow)
        self.which = QComboBox()
        self.which.setToolTip("Адрес в QR-коде и ссылке")
        self.which.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        wrow = QHBoxLayout()
        wrow.addWidget(self.which, 1)
        self.btn_copy = QPushButton()
        make_icon_button(self.btn_copy, "copy", TOKENS["text"], 20,
                         "Скопировать ссылку (в ней личный ключ слушателя — не публикуйте её)")
        wrow.addWidget(self.btn_copy)
        right.addLayout(wrow)
        krow = QHBoxLayout()
        self.key = QLabel()
        self.key.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.key.setStyleSheet(f"color:{TOKENS['text_muted']}; font-family:Consolas, monospace;")
        self.key.setToolTip("Ключ слушателя")
        krow.addWidget(self.key, 1)
        self.btn_key = QPushButton()
        make_icon_button(self.btn_key, "refresh", TOKENS["text"], 20,
                         "Сменить ключ: устройства этого слушателя отключатся, остальные — нет")
        krow.addWidget(self.btn_key)
        right.addLayout(krow)
        right.addStretch(1)
        self.clients = Line()
        self.clients.setWordWrap(True)
        self.clients.setStyleSheet(f"color:{TOKENS['text_muted']};")
        right.addWidget(self.clients)
        self.status = Line()
        self.status.setWordWrap(True)
        right.addWidget(self.status)
        body.addLayout(right, 1)
        lay.addLayout(body)

        brow = QHBoxLayout()
        self.btn_reload = QPushButton("Обновить")
        self.btn_reload.setToolTip("Заново получить слушателей и подключённые устройства с сервера")
        close = QPushButton("Закрыть")
        close.setToolTip("Закрыть окно")
        brow.addStretch(1)
        brow.addWidget(self.btn_reload)
        brow.addWidget(close)
        outer.addLayout(brow)
        self.tabs.currentChanged.connect(self._tab_changed)
        self._tab_changed(0)

        self.user.currentIndexChanged.connect(lambda _i: self._show_user())
        self.which.currentIndexChanged.connect(lambda _i: self._show_qr())
        self.btn_add.clicked.connect(self._add)
        self.btn_ren.clicked.connect(self._rename)
        self.btn_del.clicked.connect(self._remove)
        self.btn_likes.clicked.connect(self._import_likes)
        self.can_label.toggled.connect(lambda on: self._edit({"can_label": on}))
        self.btn_copy.clicked.connect(self._copy)
        self.btn_key.clicked.connect(self._new_key)
        self.btn_reload.clicked.connect(self.load)
        close.clicked.connect(self.accept)
        self._show_user()
        self.load()

    # ---- talking to the server (in the background: through the VPS it takes a moment) ------------------------------
    def _call(self, path: str, body: dict | None = None, done: str = "", select: str | None = None) -> None:
        self.status.setText("…")
        self._busy(True)

        def go():
            try:
                res, err = self.ctx.library.request(path, body, timeout=15), None
            except Exception as e:  # noqa: BLE001 - shown to the user
                res, err = None, e

            def show():
                try:
                    self._busy(False)
                    if err is not None:
                        self.status.setText(f"<span style='color:{TOKENS['danger']}'>Нет связи с сервером</span>")
                        self.status.setToolTip(str(err))
                        return
                    self._apply(res, select)
                    self.status.setText(done)
                    self.status.setToolTip("")
                except RuntimeError:                   # the dialog was closed meanwhile
                    pass
            QTimer.singleShot(0, self, show)
        threading.Thread(target=go, name="walle-listeners", daemon=True).start()

    def load(self) -> None:
        self._call("/api/listeners")

    def _edit(self, body: dict, done: str = "") -> None:
        u = self.selected_user()
        if u is not None:
            self._call(f"/api/listeners/{quote(u['id'], safe='')}", body, done)

    def _busy(self, on: bool) -> None:
        for w in (self.btn_add, self.btn_ren, self.btn_likes, self.btn_del, self.btn_key, self.btn_reload, self.can_label):
            w.setEnabled(not on)
        if not on:
            self._show_user()

    # ---- showing ------------------------------------------------------------------------------------------------------
    def _apply(self, state: dict, select: str | None = None) -> None:
        self.state = state or {}
        users = self.state.get("users") or []
        cur = select or self.user.currentData()
        if select and select not in [u["id"] for u in users]:          # a new listener: the one the server added last
            cur = users[-1]["id"] if users else None
        self.user.blockSignals(True)
        self.user.clear()
        for u in users:
            self.user.addItem(f"{u['name']} (вы)" if u.get("owner") else u["name"], u["id"])
        i = self.user.findData(cur)
        self.user.setCurrentIndex(i if i >= 0 else 0)
        self.user.blockSignals(False)
        cur_base = self.which.currentData()
        self.which.blockSignals(True)
        self.which.clear()
        for label, base in bases(self.state):
            self.which.addItem(base, base)
        i = self.which.findData(cur_base)
        self.which.setCurrentIndex(i if i >= 0 else 0)
        self.which.blockSignals(False)
        cl = self.state.get("clients") or []
        self.clients.setText("Онлайн: " + ", ".join(dict.fromkeys(c["user"] for c in cl)) if cl else "")
        self.clients.setToolTip("<br>".join(f"{c['user']} — {c['device']} ({c['address']})" for c in cl))
        self._show_user()
        if self.pages.currentIndex() == 0:
            self.adjustSize()                    # the addresses arrived: room for the longest one

    def selected_user(self) -> dict | None:
        uid = self.user.currentData()
        return next((u for u in self.state.get("users") or [] if u["id"] == uid), None)

    def _show_user(self) -> None:
        u = self.selected_user()
        guest = bool(u) and not u.get("owner")
        for w in (self.btn_del, self.btn_key, self.can_label):
            w.setEnabled(guest)
        self.btn_ren.setEnabled(bool(u))
        self.btn_likes.setEnabled(bool(u))
        self.can_label.blockSignals(True)
        self.can_label.setChecked(bool(u and u.get("can_label")))
        self.can_label.blockSignals(False)
        self._show_qr()

    def current_link(self) -> str | None:
        u, base = self.selected_user(), self.which.currentData()
        return RemoteServer.pair_url(base, u) if u and base and u.get("token") else None

    def _show_qr(self) -> None:
        url = self.current_link()
        self.btn_copy.setEnabled(bool(url))
        u = self.selected_user()
        self.key.setText(u["token"] if u and u.get("token") else "")
        if not url:
            self.qr.setPixmap(QPixmap())
            return
        self.qr.setPixmap(qr_pixmap(url).scaled(192, 192, Qt.AspectRatioMode.KeepAspectRatio,
                                                Qt.TransformationMode.FastTransformation))

    # ---- actions --------------------------------------------------------------------------------------------------------
    def _add(self) -> None:
        name, ok = QInputDialog.getText(self, "Новый слушатель", "Имя:")
        if ok and name.strip():
            self._call("/api/listeners", {"name": name.strip()}, select="new")

    def _rename(self) -> None:
        u = self.selected_user()
        if not u:
            return
        name, ok = QInputDialog.getText(self, "Переименовать", "Имя слушателя:", text=u["name"])
        if ok and name.strip():
            self._edit({"name": name.strip()})

    def _remove(self) -> None:
        u = self.selected_user()
        if u and not u.get("owner") and QMessageBox.question(
                self, "Удалить слушателя", f"«{u['name']}» потеряет доступ на всех своих устройствах. Удалить?") \
                == QMessageBox.StandardButton.Yes:
            self._edit({"remove": True})

    def _new_key(self) -> None:
        u = self.selected_user()
        if u and QMessageBox.question(self, "Сменить ключ", f"Устройства слушателя «{u['name']}» потеряют доступ, "
                                      "их нужно будет подключить заново по новому QR-коду. Сменить?") \
                == QMessageBox.StandardButton.Yes:
            self._edit({"new_key": True}, "Ключ сменён")

    def _import_likes(self) -> None:
        """The file is read here, the server matches it with its catalogue and keeps the likes."""
        from musicplayer.ui.likeimport_dialog import import_likes
        u = self.selected_user()
        if not u:
            return
        path = f"/api/listeners/{quote(u['id'], safe='')}/likes"

        def remote(items: list[dict], dry_run: bool) -> dict:
            QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                return self.ctx.library.request(path, {"items": items, "dry_run": dry_run}, timeout=120)
            except OSError as e:                     # not «the file was not read»: the server did not answer
                raise RuntimeError(str(e)) from e
            finally:
                QGuiApplication.restoreOverrideCursor()
        n = import_likes(self.ctx, u, self, remote)
        if n:
            self.status.setText(f"Добавлено лайков: {n}")

    def _copy(self) -> None:
        url = self.current_link()
        if url:
            QGuiApplication.clipboard().setText(url)
            self.status.setText("Ссылка скопирована")

    def _tab_changed(self, i: int) -> None:
        """The window takes the size of the shown tab (the server's tab is much smaller than «Этот компьютер»)."""
        from PySide6.QtWidgets import QSizePolicy
        for k in range(self.pages.count()):
            pol = QSizePolicy.Policy.Preferred if k == i else QSizePolicy.Policy.Ignored
            self.pages.widget(k).setSizePolicy(pol, pol)
        self.pages.setCurrentIndex(i)
        self.btn_reload.setVisible(i == 0)
        self.pages.adjustSize()
        self.adjustSize()

    def done(self, r: int) -> None:
        if self.local is not None:
            self.local.done(r)                   # its timer and signal connections
        super().done(r)
