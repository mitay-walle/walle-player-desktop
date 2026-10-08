"""«Другие устройства»: the library server for phones and other computers - on/off, listeners, QR, internet tunnel."""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from musicplayer.server import config
from musicplayer.server.relay import SETUP_COMMAND, Relay, ensure_key, https_command, sslip_name
from musicplayer.server.upnp import Router, is_grey
from musicplayer.server.runner import (RemoteServer, Tunnel, find_cloudflared, global_ipv6, lan_addresses,
                                       network_problems)
from musicplayer.ui.theme import TOKENS

log = logging.getLogger(__name__)


def qr_pixmap(text: str, scale: int = 6) -> QPixmap:
    import io

    import segno
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="png", scale=scale, border=3, dark="#111113", light="#E6E3DA")
    pm = QPixmap()
    pm.loadFromData(buf.getvalue(), "PNG")
    return pm


class RemoteSharing(QObject):
    """Owns the server, the listeners and the tunnel for the desktop app; changes from phones refresh the pages."""

    changed = Signal()                     # server/tunnel/listeners changed (from any thread)
    join_requested = Signal(dict)          # a new device asks for access (from the server thread)
    banned = Signal(dict)                  # an address was banned for wrong keys (from the server thread)
    _tracks = Signal(list)
    _users = Signal()

    def __init__(self, ctx) -> None:
        super().__init__()
        self.ctx = ctx
        self.cfg = config.load(ctx.home)
        self.server: RemoteServer | None = None
        self.tunnel: Tunnel | None = None
        self.tunnel_busy = False
        self.notice = ""                       # e.g. "the port was busy, the server took the next one"
        self.ipv6: list[str] = []              # internet IPv6 addresses (filled in the background)
        self.relay: Relay | None = None        # the VPS tunnel (internet mode «relay»)
        self.router_ip: str | None = None      # the router's internet address read over UPnP
        self.mapped: tuple[Router, int] | None = None   # the port opened on the router (closed on stop)
        self.net_state = ""                    # what the internet access is doing, for the dialog
        self._tracks.connect(lambda ids: ctx.events.tracks_changed.emit(ids))
        self._users.connect(lambda: (config.save(self.ctx.home, self.cfg), self.changed.emit()))

    @property
    def running(self) -> bool:
        return self.server is not None and self.server.running

    def _save(self) -> None:
        config.save(self.ctx.home, self.cfg)
        if self.server is not None:
            self.server.set_users(self.cfg.users)
        self.changed.emit()

    def start(self) -> bool:
        if self.running:
            return True
        wanted, ok = self.cfg.port, False
        for port in range(wanted, wanted + 10):     # another program holds the port: take the next free one
            self.server = RemoteServer(self.ctx.db_path, self.ctx.covers, self.cfg.users, port,
                                       on_change=lambda ids: self._tracks.emit(list(ids)), on_users=self._users.emit,
                                       on_join=self.join_requested.emit if self.cfg.allow_join else None,
                                       library_id=self.cfg.library_id, on_ban=self.banned.emit)
            ok = self.server.start()
            if ok or not self.server.port_busy:
                break
        self.notice = ""
        if ok and self.server.port != wanted:          # only for this run: the saved links keep the usual port
            self.notice = (f"Порт {wanted} занят другой программой — сейчас сервер работает на {self.server.port}, "
                           f"ссылки и QR с портом {wanted} не откроются, пока он занят. Закройте программу, которая его "
                           f"держит, и перезапустите доступ.")
        self.cfg.enabled = ok or self.cfg.enabled
        self._save()
        if ok:
            self.server.access.addresses = self.addresses
            self.refresh_ipv6()
            self.apply_net()
        if ok and self.cfg.tunnel:
            self.start_tunnel()
        return ok

    def refresh_ipv6(self) -> None:
        """Looks for an internet IPv6 address in the background (Windows takes a couple of seconds to answer)."""
        def go():
            ips = global_ipv6()
            if ips != self.ipv6:
                self.ipv6 = ips
                self.changed.emit()
        threading.Thread(target=go, name="walle-ipv6", daemon=True).start()

    # ---- internet access: static IP (router, UPnP) or own VPS (reverse SSH tunnel) -----------------------------
    def addresses(self) -> dict:
        """Every base address of the library, for clients: paired at home, a phone learns the internet one too."""
        if not self.running:
            return {"lan": [], "internet": []}
        internet = [b for label, b in self.bases() if label.startswith("Из интернета") or label.startswith("Интернет")]
        return {"lan": self.server.urls(), "internet": internet}

    def public_base(self) -> str | None:
        """The address phones use from outside in the chosen mode, or None."""
        m = self.cfg.net_mode
        if m == "manual":
            return self.cfg.public_url or None
        if m == "static":
            host = self.cfg.static_host.strip() or (self.router_ip if not is_grey(self.router_ip) else None)
            if not host:                                   # a grey router address would only mislead in the QR
                return None
            if ":" in host and not host.startswith("["):
                host = f"[{host}]"
            return f"http://{host}:{self.cfg.static_port}"
        if m == "relay" and self.cfg.relay_https.strip():
            return f"https://{self.cfg.relay_https.strip()}"
        if m == "relay" and self.cfg.relay_host.strip():
            return f"http://{self.cfg.relay_host.strip()}:{self.cfg.relay_port}"
        return None

    def set_net(self, **fields) -> None:
        """Changes the internet settings and applies them at once (when the server runs)."""
        changed = {k: v for k, v in fields.items() if getattr(self.cfg, k) != v}
        if not changed:
            return
        for k, v in changed.items():
            setattr(self.cfg, k, v)
        config.save(self.ctx.home, self.cfg)
        if self.running:
            self.apply_net()
        self.changed.emit()

    def _net_state(self, text: str) -> None:
        self.net_state = text
        self.changed.emit()

    def _net_down(self) -> None:
        if self.relay is not None:
            self.relay.stop()
            self.relay = None
        if self.server is not None:
            self.server.access.loopback_key = None
        if self.mapped is not None:
            router, port = self.mapped
            self.mapped = None
            threading.Thread(target=lambda: _quiet(router.remove_port, port), name="walle-upnp-close",
                             daemon=True).start()

    def apply_net(self) -> None:
        self._net_down()
        self.net_state = ""
        m = self.cfg.net_mode
        if not self.running or m in ("off", "manual"):
            self.changed.emit()
            return
        if m == "relay":
            if not self.cfg.relay_host.strip():
                self._net_state("Укажите адрес своего сервера (VPS).")
                return
            self.server.access.loopback_key = "через VPS"
            self.relay = Relay(self.ctx.home, self.cfg.relay_host, self.cfg.relay_user, self.server.port,
                               self.cfg.relay_port, self.cfg.relay_ssh_port, on_change=self._relay_changed,
                               bind="127.0.0.1" if self.cfg.relay_https.strip() else "0.0.0.0")
            self.relay.start()
            self._relay_changed()
            return
        lan = (self.server.urls() or ["http://"])[0].split("//")[1].rsplit(":", 1)[0]
        port, local, upnp = self.cfg.static_port, self.server.port, self.cfg.upnp
        self._net_state("Ищу роутер…")

        def go():
            router = Router.find(lan if lan[0].isdigit() else None)
            if router is None:
                self.router_ip = None
                self._net_state("Роутер не ответил по UPnP: откройте порт на роутере вручную "
                                f"(внешний {port} → {lan}:{local}) или включите UPnP в роутере.")
                return
            try:
                self.router_ip = router.external_ip()
            except Exception as e:  # noqa: BLE001
                self.router_ip = None
                self._net_state(f"Роутер не сообщил внешний адрес: {e}")
                return
            if is_grey(self.router_ip):
                self._net_state(f"Внешний адрес роутера {self.router_ip} — «серый» (общий у провайдера): снаружи до "
                                "компьютера не достучаться. Нужен статический (белый) IP у провайдера.")
                return
            if not upnp:
                self._net_state(f"Внешний IP роутера: {self.router_ip}. Откройте на роутере порт {port} → {lan}:{local}.")
                return
            try:
                router.add_port(port, local, lan)
                self.mapped = (router, port)
                self._net_state(f"Порт {port} открыт на роутере, внешний IP {self.router_ip}.")
            except Exception as e:  # noqa: BLE001
                self._net_state(f"Роутер не открыл порт {port}: {e}. Откройте его вручную: {port} → {lan}:{local}.")
        threading.Thread(target=go, name="walle-upnp", daemon=True).start()

    def _relay_changed(self) -> None:
        r = self.relay
        if r is None:
            return
        text = {"connecting": f"Подключаюсь к серверу {r.host}…",
                "connected": f"Туннель к серверу работает: {self.public_base() or r.url}",
                "error": f"Туннель: {r.error}", "stopped": ""}.get(r.state, "")
        self._net_state(text)

    def relay_public_key(self) -> str:
        return ensure_key(self.ctx.home)[1]

    def stop(self, remember: bool = True) -> None:
        self._net_down()
        self.stop_tunnel(remember=False)
        if self.server is not None:
            self.server.stop()
            self.server = None
        if remember:
            self.cfg.enabled = False
            config.save(self.ctx.home, self.cfg)
        self.changed.emit()

    # ---- listeners ------------------------------------------------------------
    def add_user(self, name: str, can_label: bool = False) -> dict:
        u = config.add_user(self.cfg, name, can_label)
        self._save()
        return u

    def remove_user(self, uid: str) -> None:
        config.remove_user(self.cfg, uid)
        self._save()

    def set_allow_join(self, on: bool) -> None:
        self.cfg.allow_join = bool(on)
        if self.server is not None:
            self.server.access.on_join = self.join_requested.emit if on else None
        self._save()

    def answer_join(self, req: dict, user_id: str | None, can_label: bool = False) -> dict | None:
        """Lets the device in as an existing listener (``user_id``), as a new one ("new") or refuses it (None)."""
        if self.server is None:
            return None
        user = None
        if user_id == "new":
            user = self.add_user(req["name"], can_label)
        elif user_id:
            user = self.cfg.user(user_id)
        self.server.access.answer_join(req["id"], user)
        return user

    def rename_user(self, uid: str, name: str) -> None:
        u = self.cfg.user(uid)
        if u is not None and name.strip():
            u["name"] = " ".join(name.split())[:40]
            self._save()

    def set_can_label(self, uid: str, on: bool) -> None:
        u = self.cfg.user(uid)
        if u is not None and not u.get("owner"):
            u["can_label"] = bool(on)
            self._save()

    def new_key(self, uid: str) -> None:
        """A new key for one listener: that person's devices lose access, the others keep it."""
        u = self.cfg.user(uid)
        if u is not None:
            u["token"] = config.new_token()
            self._save()

    # ---- internet -------------------------------------------------------------------
    def start_tunnel(self) -> None:
        """Runs in the background (cloudflared needs a few seconds to get the address)."""
        if not self.running or self.tunnel_busy or (self.tunnel and self.tunnel.running):
            return
        self.tunnel = Tunnel(self.server.port, find_cloudflared(self.ctx.home))
        self.tunnel_busy = True
        self.cfg.tunnel = True
        config.save(self.ctx.home, self.cfg)
        self.changed.emit()

        def go():
            self.tunnel.start()
            self.tunnel_busy = False
            self.changed.emit()
        threading.Thread(target=go, name="walle-tunnel-start", daemon=True).start()

    def stop_tunnel(self, remember: bool = True) -> None:
        if self.tunnel is not None:
            self.tunnel.stop()
        if remember:
            self.cfg.tunnel = False
            config.save(self.ctx.home, self.cfg)
        self.changed.emit()

    def set_public_url(self, url: str) -> None:
        url = url.strip().rstrip("/")
        if url and "://" not in url:
            url = "https://" + url
        self.cfg.public_url = url
        self._save()

    def bases(self) -> list[tuple[str, str]]:
        """[(label, base url)]: the internet addresses first, then the LAN ones."""
        if not self.running:
            return []
        label = {"static": "Из интернета (статический IP)", "relay": "Из интернета (через VPS)"}.get(
            self.cfg.net_mode, "Из интернета")
        base = self.public_base()
        out = [(label, base)] if base else []
        if not base and self.ipv6:                              # an internet IPv6 address works without a router setup
            out.append(("Из интернета (IPv6)", f"http://[{self.ipv6[0]}]:{self.server.port}"))
        if self.tunnel and self.tunnel.url:
            out.append(("Интернет (Cloudflare)", self.tunnel.url))
        return out + [("Домашняя сеть", u) for u in self.server.urls()]

    def status(self) -> dict:
        s = self.server
        bases = self.bases()
        return {"running": self.running, "port": s.port if self.running else self.cfg.port, "error": s.error if s else None,
                "net_mode": self.cfg.net_mode, "net_state": self.net_state, "public": self.public_base(),
                "library": self.cfg.library_id,
                "listeners": [{"id": u["id"], "name": u["name"], "owner": bool(u.get("owner")),
                               "can_label": bool(u.get("can_label")),
                               "links": [RemoteServer.pair_url(b, u) for _, b in bases]} for u in self.cfg.users],
                "internet": self.tunnel.url if self.running and self.tunnel and self.tunnel.url else None,
                "tunnel_error": self.tunnel.error if self.tunnel else None,
                "cloudflared": str(find_cloudflared(self.ctx.home) or ""),
                "clients": s.clients() if self.running else []}


class JoinDialog(QDialog):
    """«Новое устройство просит доступ»: let it in as a new listener or as an existing one (e.g. the owner's second
    phone), or refuse.  Not modal: the music keeps playing; closing it without an answer leaves the request waiting."""

    def __init__(self, sharing: RemoteSharing, req: dict, parent=None) -> None:
        super().__init__(parent)
        self.sharing, self.req = sharing, req
        self.setWindowTitle("Новое устройство просит доступ")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>{req['name']}</b> просит доступ к вашей библиотеке<br>"
                      f"<span style='color:{TOKENS['text_muted']}'>{req['device']} · {req['address']}</span>")
        head.setWordWrap(True)
        lay.addWidget(head)
        row = QHBoxLayout()
        row.addWidget(QLabel("Пустить как:"))
        self.who = QComboBox()
        self.who.addItem(f"нового слушателя «{req['name']}»", "new")
        for u in sharing.cfg.users:
            self.who.addItem(f"{u['name']}" + (" (вы — ваше устройство)" if u.get("owner") else " — его устройство"), u["id"])
        row.addWidget(self.who, 1)
        lay.addLayout(row)
        self.can_label = QCheckBox("Может размечать настроения и жанры")
        lay.addWidget(self.can_label)
        self.who.currentIndexChanged.connect(lambda _i: self.can_label.setVisible(self.who.currentData() == "new"))
        btns = QHBoxLayout()
        no = QPushButton("Отклонить")
        yes = QPushButton("Разрешить")
        yes.setObjectName("Primary")
        yes.setDefault(True)
        btns.addStretch(1)
        btns.addWidget(no)
        btns.addWidget(yes)
        lay.addLayout(btns)
        yes.clicked.connect(lambda: self._answer(self.who.currentData()))
        no.clicked.connect(lambda: self._answer(None))

    def _answer(self, user_id: str | None) -> None:
        user = self.sharing.answer_join(self.req, user_id, self.can_label.isChecked())
        win = self.parent()
        if win is not None and hasattr(win, "statusBar"):
            win.statusBar().showMessage(f"Доступ разрешён: {self.req['device']} — {user['name']}" if user
                                        else f"Доступ отклонён: {self.req['name']}", 8000)
        self.accept()


class ShareDialog(QDialog):
    def __init__(self, sharing: RemoteSharing, parent=None) -> None:
        super().__init__(parent)
        self.sharing = sharing
        self.setWindowTitle("Другие устройства")
        self.setMinimumWidth(600)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        intro = QLabel("<b>Слушайте библиотеку на телефоне и других компьютерах</b> — дома по Wi-Fi и через "
                       "интернет. У каждого слушателя свой QR-код: свои лайки и история, ваше «Избранное» и "
                       "статистика не смешиваются с чужими.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.enable = QCheckBox("Открыть библиотеку для других устройств")
        lay.addWidget(self.enable)

        urow = QHBoxLayout()
        urow.addWidget(QLabel("Слушатель:"))
        self.user = QComboBox()
        urow.addWidget(self.user, 1)
        self.btn_add = QPushButton("Добавить…")
        self.btn_add.setToolTip("Новый слушатель со своим QR-кодом: свои лайки и история прослушиваний")
        self.btn_del = QPushButton("Удалить")
        self.btn_ren = QPushButton("Переименовать…")
        urow.addWidget(self.btn_add)
        urow.addWidget(self.btn_ren)
        urow.addWidget(self.btn_del)
        lay.addLayout(urow)
        self.can_label = QCheckBox("Может размечать настроения и жанры (это учит вашу модель настроений)")
        lay.addWidget(self.can_label)
        self.allow_join = QCheckBox("Новые устройства могут попросить доступ — без QR, вы подтверждаете здесь")
        self.allow_join.setChecked(sharing.cfg.allow_join)
        self.allow_join.toggled.connect(sharing.set_allow_join)
        lay.addWidget(self.allow_join)
        from musicplayer.ui import tray
        self.autostart = QCheckBox("Запускать вместе с Windows — в трее, библиотека сразу открыта для устройств")
        self.autostart.setChecked(tray.autostart_enabled())
        self.autostart.setEnabled(tray.autostart_supported() or tray.autostart_enabled())
        if not tray.autostart_supported():
            self.autostart.setToolTip("Работает в собранной программе (WallePlayer.exe)")
        self.autostart.toggled.connect(lambda on: self.autostart.setChecked(tray.set_autostart(on) == on and on))
        lay.addWidget(self.autostart)
        hint = QLabel("Пока библиотека открыта для устройств, закрытие окна сворачивает Walle Player в трей — "
                      "сервер и музыка продолжают работать. Выйти — правой кнопкой по значку в трее → «Выход».")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{TOKENS['text_muted']}; font-size:12px;")
        lay.addWidget(hint)

        body = QHBoxLayout()
        self.qr = QLabel()
        self.qr.setFixedSize(236, 236)
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr.setStyleSheet(f"background:{TOKENS['surface2']}; border-radius:12px;")
        body.addWidget(self.qr)
        right = QVBoxLayout()
        self.which = QComboBox()
        self.which.setToolTip("Какой адрес показать в QR-коде")
        right.addWidget(QLabel("Адрес:"))
        right.addWidget(self.which)
        self.url = QLabel()
        self.url.setWordWrap(True)
        self.url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.url.setStyleSheet(f"color:{TOKENS['text_muted']};")
        right.addWidget(self.url)
        row = QHBoxLayout()
        self.btn_copy = QPushButton("Скопировать ссылку")
        self.btn_key = QPushButton("Сменить ключ…")
        self.btn_key.setToolTip("Новый ключ этого слушателя: его устройства отключатся, остальные — нет")
        row.addWidget(self.btn_copy)
        row.addWidget(self.btn_key)
        right.addLayout(row)
        self.hint = QLabel("Наведите камеру телефона на QR-код — откроется плеер. В браузере телефона: "
                           "«Добавить на главный экран», и он будет как приложение.")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color:{TOKENS['text_muted']}; font-size:12px;")
        right.addWidget(self.hint)
        right.addStretch(1)
        body.addLayout(right, 1)
        lay.addLayout(body)

        lay.addWidget(self._internet_box(sharing))
        nrow = QHBoxLayout()
        self.btn_tunnel = QPushButton()
        self.tunnel_state = QLabel()
        self.tunnel_state.setWordWrap(True)
        self.tunnel_state.setOpenExternalLinks(True)
        nrow.addWidget(self.btn_tunnel)
        nrow.addWidget(self.tunnel_state, 1)
        lay.addLayout(nrow)

        self.clients = QLabel()
        self.clients.setWordWrap(True)
        self.clients.setStyleSheet(f"color:{TOKENS['text_muted']};")
        lay.addWidget(self.clients)
        brow = QHBoxLayout()
        self.bans = QLabel()                   # fail2ban: addresses blocked for guessing keys
        self.bans.setWordWrap(True)
        self.bans.setStyleSheet(f"color:{TOKENS['danger']};")
        self.btn_unban = QPushButton("Снять блокировки")
        self.btn_unban.setToolTip("Если это вы ошиблись ключом — пустить адрес снова")
        self.btn_unban.clicked.connect(self._unban)
        brow.addWidget(self.bans, 1)
        brow.addWidget(self.btn_unban, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(brow)
        self.bans.hide()
        self.btn_unban.hide()
        wrow = QHBoxLayout()
        self.warn = QLabel()                   # why the phone may not see the computer (public network, firewall)
        self.warn.setWordWrap(True)
        self.warn.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.warn.setStyleSheet(f"color:{TOKENS['warning']};")
        self.btn_recheck = QPushButton("Проверить ещё раз")
        self.btn_recheck.clicked.connect(self._check_network)
        wrow.addWidget(self.warn, 1)
        wrow.addWidget(self.btn_recheck, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(wrow)
        self.warn.hide()
        self.btn_recheck.hide()
        self._checked = False
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        lay.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)

        self.enable.toggled.connect(self._toggle)
        self.user.currentIndexChanged.connect(lambda _i: self._show_user())
        self.which.currentIndexChanged.connect(lambda _i: self._show_qr())
        self.btn_add.clicked.connect(self._add_user)
        self.btn_del.clicked.connect(self._del_user)
        self.btn_ren.clicked.connect(self._rename_user)
        self.can_label.toggled.connect(lambda on: self.sharing.set_can_label(self.user.currentData(), on))
        self.btn_copy.clicked.connect(self._copy)
        self.btn_key.clicked.connect(self._new_key)
        self.btn_tunnel.clicked.connect(self._tunnel)
        sharing.changed.connect(self._refresh_soon)
        if sharing.running:
            sharing.refresh_ipv6()                 # IPv6 may have appeared since the start (router set up later)
        self._timer = QTimer(self, interval=3000)
        self._timer.timeout.connect(self._refresh_clients)
        self._timer.start()
        self._refresh()

    def _refresh_soon(self) -> None:
        QTimer.singleShot(0, self._refresh)            # the signal may come from the tunnel thread

    def _toggle(self, on: bool) -> None:
        if on == self.sharing.running:
            return
        if on:
            QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                ok = self.sharing.start()
            finally:
                QGuiApplication.restoreOverrideCursor()
            if not ok:
                QMessageBox.warning(self, "Другие устройства", f"Сервер не запустился: {self.sharing.server.error}")
        else:
            self.sharing.stop()
        self._refresh()

    def selected_user(self) -> dict | None:
        return self.sharing.cfg.user(self.user.currentData() or "")

    def _refresh(self) -> None:
        sh = self.sharing
        running = sh.running
        self.enable.blockSignals(True)
        self.enable.setChecked(running)
        self.enable.blockSignals(False)
        cur = self.user.currentData()
        self.user.blockSignals(True)
        self.user.clear()
        for u in sh.cfg.users:
            self.user.addItem(f"{u['name']} (вы, владелец)" if u.get("owner") else u["name"], u["id"])
        i = self.user.findData(cur)
        self.user.setCurrentIndex(i if i >= 0 else 0)
        self.user.blockSignals(False)
        cur_base = self.which.currentData()
        self.which.blockSignals(True)
        self.which.clear()
        for label, base in sh.bases():
            self.which.addItem(f"{label}: {base}", base)
        i = self.which.findData(cur_base)
        self.which.setCurrentIndex(i if i >= 0 else 0)
        self.which.blockSignals(False)
        for w in (self.which, self.btn_copy, self.btn_key, self.btn_tunnel):
            w.setEnabled(running)
        self._show_user()
        if not self.public.hasFocus():
            self.public.setText(sh.cfg.public_url)
        self.btn_check.setEnabled(running and bool(sh.public_base()))
        self.net_state.setText(sh.net_state if running else "Откройте библиотеку для устройств (галочка вверху).")
        t = sh.tunnel
        has_cf = bool(find_cloudflared(sh.ctx.home))     # optional: Cloudflare is unreliable in Russia
        self.btn_tunnel.setVisible(has_cf)
        self.tunnel_state.setVisible(has_cf)
        if sh.tunnel_busy:
            self.btn_tunnel.setText("Открываю…")
            self.btn_tunnel.setEnabled(False)
            self.tunnel_state.setText("Получаю адрес у Cloudflare (до 30 секунд)…")
        elif t is not None and t.running and t.url:
            self.btn_tunnel.setText("Закрыть доступ из интернета")
            self.tunnel_state.setText(f"Открыт: {t.url}")
        else:
            self.btn_tunnel.setText("Временный адрес Cloudflare")
            if t is not None and t.error:
                self.tunnel_state.setText(f"Не получилось: {t.error}")
            else:
                self.tunnel_state.setText("")
        err = sh.server.error if sh.server is not None and not running else ""
        self.status.setText((f"<span style='color:{TOKENS['danger']}'>Ошибка: {err}</span>" if err else
                             sh.notice or "Если Windows спросит про брандмауэр — разрешите доступ в частных сетях."
                             if running else "Сейчас библиотека доступна только на этом компьютере."))
        if running and not self._checked:
            self._check_network()
        elif not running:
            self.warn.hide()
            self.btn_recheck.hide()
            self._checked = False
        self._refresh_clients()

    def _check_network(self) -> None:
        """Public network / no firewall permission: checked in the background, shown with what to do."""
        self._checked = True
        self.btn_recheck.setEnabled(False)

        def go():
            ips = lan_addresses()
            problems = network_problems(ips[0] if ips else None)

            def show():
                try:
                    self.btn_recheck.setEnabled(True)
                    self.warn.setVisible(bool(problems))
                    self.btn_recheck.setVisible(bool(problems))
                    self.warn.setText("Телефон может не увидеть компьютер:\n• " + "\n• ".join(problems) if problems else "")
                except RuntimeError:           # the dialog was closed meanwhile
                    pass
            QTimer.singleShot(0, self, show)
        threading.Thread(target=go, name="walle-netcheck", daemon=True).start()

    def _show_user(self) -> None:
        u = self.selected_user()
        owner = bool(u and u.get("owner"))
        self.btn_del.setEnabled(bool(u) and not owner)
        self.can_label.blockSignals(True)
        self.can_label.setChecked(bool(u and u.get("can_label")))
        self.can_label.setEnabled(bool(u) and not owner)
        self.can_label.blockSignals(False)
        self._show_qr()

    def _refresh_bans(self) -> None:
        srv = self.sharing.server
        bans = srv.access.banned() if self.sharing.running else []
        self.bans.setVisible(bool(bans))
        self.btn_unban.setVisible(bool(bans))
        if bans:
            import time as _t
            rows = [f"• {b['key']} — до {_t.strftime('%d.%m %H:%M', _t.localtime(b['until']))}"
                    + (f" (уже {b['strikes']}-й раз)" if b["strikes"] > 1 else "") for b in bans[:6]]
            self.bans.setText("Заблокированы за подбор ключа:<br>" + "<br>".join(rows))

    def _unban(self) -> None:
        if self.sharing.server is not None:
            self.sharing.server.access.unban()
        self._refresh_bans()

    def _refresh_clients(self) -> None:
        self._refresh_bans()
        cl = self.sharing.status()["clients"]
        if not cl:
            self.clients.setText("Подключённых устройств нет." if self.sharing.running else "")
            return
        rows = [f"• {c['user']} — {c['device']} ({c['address']})" + (f": {c['track']}" if c.get("track") else "")
                for c in cl]
        self.clients.setText("Сейчас подключены:<br>" + "<br>".join(rows))

    def current_link(self) -> str | None:
        u, base = self.selected_user(), self.which.currentData()
        return RemoteServer.pair_url(base, u) if u and base else None

    def _show_qr(self) -> None:
        url = self.current_link()
        if not url:
            self.qr.setPixmap(QPixmap())
            self.qr.setText("QR-код появится,\nкогда доступ открыт")
            self.url.setText("")
            return
        self.qr.setPixmap(qr_pixmap(url).scaled(226, 226, Qt.AspectRatioMode.KeepAspectRatio,
                                                Qt.TransformationMode.FastTransformation))
        who = self.selected_user()["name"]
        self.url.setText(f"QR-код для: {who}\nСсылка содержит личный ключ — не публикуйте её.")

    # ---- «Из интернета»: the way phones reach the computer from outside -------------------------------------------
    MODES = [("off", "Выключен — только домашняя сеть"), ("static", "Статический IP — через роутер"),
             ("relay", "Свой сервер (VPS) — туннель"), ("manual", "Свой адрес (KeenDNS, сервис-туннель…)")]

    def _internet_box(self, sh: RemoteSharing) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 6, 0, 0)
        head = QHBoxLayout()
        head.addWidget(QLabel("<b>Из интернета:</b>"))
        self.net_mode = QComboBox()
        for key, label in self.MODES:
            self.net_mode.addItem(label, key)
        self.net_mode.setCurrentIndex(max(0, self.net_mode.findData(sh.cfg.net_mode)))
        head.addWidget(self.net_mode, 1)
        self.btn_check = QPushButton("Проверить")
        self.btn_check.setToolTip("Открывается ли сервер по внешнему адресу (запрос с этого компьютера; для "
                                  "статического IP надёжнее проверить с телефона без Wi-Fi)")
        self.btn_check.clicked.connect(self._check_public)
        head.addWidget(self.btn_check)
        v.addLayout(head)
        self.net_pages = QStackedWidget()
        muted = f"color:{TOKENS['text_muted']}; font-size:12px;"

        off = QLabel("Библиотека открыта только в домашней сети. Для мобильного интернета нужен статический IP у "
                     "провайдера (~200 ₽/мес) или свой небольшой сервер VPS (~100–150 ₽/мес). Подробнее: "
                     "docs/internet-access.md.")
        off.setWordWrap(True)
        off.setStyleSheet(muted)

        static = QWidget()
        sf = QFormLayout(static)
        sf.setContentsMargins(0, 0, 0, 0)
        self.static_host = QLineEdit(sh.cfg.static_host)
        self.static_host.setPlaceholderText("пусто — внешний IP роутера; или DDNS-имя (music.example.ru)")
        self.static_port = QSpinBox()
        self.static_port.setRange(1024, 65535)
        self.static_port.setValue(sh.cfg.static_port)
        self.upnp = QCheckBox("Открыть порт на роутере автоматически (UPnP)")
        self.upnp.setChecked(sh.cfg.upnp)
        sf.addRow("Адрес:", self.static_host)
        sf.addRow("Внешний порт:", self.static_port)
        sf.addRow("", self.upnp)

        relay = QWidget()
        rf = QFormLayout(relay)
        rf.setContentsMargins(0, 0, 0, 0)
        self.relay_host = QLineEdit(sh.cfg.relay_host)
        self.relay_host.setPlaceholderText("IP или имя сервера, например 185.12.34.56")
        self.relay_user = QLineEdit(sh.cfg.relay_user)
        self.relay_ssh = QSpinBox()
        self.relay_ssh.setRange(1, 65535)
        self.relay_ssh.setValue(sh.cfg.relay_ssh_port)
        self.relay_port = QSpinBox()
        self.relay_port.setRange(1024, 65535)
        self.relay_port.setValue(sh.cfg.relay_port)
        keys = QHBoxLayout()
        b_key = QPushButton("Скопировать ключ для сервера")
        b_key.setToolTip("Вставьте его на сервере в ~/.ssh/authorized_keys (одна строка)")
        b_cmd = QPushButton("Скопировать команду настройки")
        b_cmd.setToolTip("Выполните её на сервере один раз: разрешает серверу принимать телефоны на порту туннеля")
        b_key.clicked.connect(self._copy_relay_key)
        b_cmd.clicked.connect(self._copy_relay_setup)
        keys.addWidget(b_key)
        keys.addWidget(b_cmd)
        rf.addRow("Сервер:", self.relay_host)
        rf.addRow("Пользователь SSH:", self.relay_user)
        rf.addRow("Порт SSH:", self.relay_ssh)
        rf.addRow("Порт для телефонов:", self.relay_port)
        self.relay_https = QLineEdit(sh.cfg.relay_https)
        self.relay_https.setPlaceholderText("имя для https, например 203-0-113-10.sslip.io (Caddy на сервере)")
        self.relay_https.setToolTip("Пусто — телефоны идут на сервер по http. С именем — по https через Caddy на "
                                    "сервере, туннель открыт только внутри сервера")
        rf.addRow("HTTPS-имя:", self.relay_https)
        rf.addRow("", keys)
        b_https = QPushButton("Скопировать команду https (Caddy)")
        b_https.setToolTip("Выполните её на сервере один раз: Caddy сам получит сертификат на это имя")
        b_https.clicked.connect(self._copy_https_setup)
        rf.addRow("", b_https)
        steps = QLabel("Один раз на сервере: ключ — в ~/.ssh/authorized_keys, затем команда настройки; в брандмауэре "
                       "сервера открыть порт для телефонов. Дальше Walle Player держит туннель сам.")
        steps.setWordWrap(True)
        steps.setStyleSheet(muted)
        rf.addRow(steps)

        manual = QWidget()
        mh = QHBoxLayout(manual)
        mh.setContentsMargins(0, 0, 0, 0)
        self.public = QLineEdit(sh.cfg.public_url)
        self.public.setPlaceholderText("https://music.ваш-домен.keenetic.pro")
        self.public.setClearButtonEnabled(True)
        mh.addWidget(QLabel("Внешний адрес:"))
        mh.addWidget(self.public, 1)

        for w in (off, static, relay, manual):
            self.net_pages.addWidget(w)
        v.addWidget(self.net_pages)
        self.net_state = QLabel()
        self.net_state.setWordWrap(True)
        self.net_state.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.net_state)
        from PySide6.QtWidgets import QSizePolicy

        def show_page(i: int) -> None:                    # the stack takes the height of the shown page only
            for k in range(self.net_pages.count()):
                pol = QSizePolicy.Policy.Preferred if k == i else QSizePolicy.Policy.Ignored
                self.net_pages.widget(k).setSizePolicy(QSizePolicy.Policy.Preferred, pol)
            self.net_pages.setCurrentIndex(i)
            self.net_pages.adjustSize()
            self.adjustSize()
        show_page(self.net_mode.currentIndex())

        def mode_changed(_i):
            show_page(self.net_mode.currentIndex())
            sh.set_net(net_mode=self.net_mode.currentData())
        self.net_mode.currentIndexChanged.connect(mode_changed)
        self.static_host.editingFinished.connect(lambda: sh.set_net(static_host=self.static_host.text().strip()))
        self.static_port.editingFinished.connect(lambda: sh.set_net(static_port=self.static_port.value()))
        self.upnp.toggled.connect(lambda on: sh.set_net(upnp=on))
        self.relay_host.editingFinished.connect(lambda: sh.set_net(relay_host=self.relay_host.text().strip()))
        self.relay_user.editingFinished.connect(lambda: sh.set_net(relay_user=self.relay_user.text().strip() or "root"))
        self.relay_ssh.editingFinished.connect(lambda: sh.set_net(relay_ssh_port=self.relay_ssh.value()))
        self.relay_port.editingFinished.connect(lambda: sh.set_net(relay_port=self.relay_port.value()))
        self.relay_https.editingFinished.connect(lambda: sh.set_net(relay_https=self.relay_https.text().strip()))
        self.public.editingFinished.connect(lambda: sh.set_public_url(self.public.text())
                                            if self.public.text().strip().rstrip("/") != sh.cfg.public_url else None)
        return box

    def _copy_relay_key(self) -> None:
        try:
            QGuiApplication.clipboard().setText(self.sharing.relay_public_key())
            self.status.setText("Ключ скопирован: вставьте его на сервере отдельной строкой в ~/.ssh/authorized_keys.")
        except Exception as e:  # noqa: BLE001
            self.status.setText(f"Ключ не создан: {e}")

    def _copy_https_setup(self) -> None:
        sh = self.sharing
        name = sh.cfg.relay_https.strip() or sslip_name(sh.cfg.relay_host)
        if not name:
            self.status.setText("Укажите адрес сервера (IP) или HTTPS-имя.")
            return
        if not sh.cfg.relay_https.strip():
            self.relay_https.setText(name)
        QGuiApplication.clipboard().setText(https_command(name, sh.cfg.relay_port))
        self.status.setText(f"Команда скопирована: выполните её на сервере (root), потом впишите «{name}» в HTTPS-имя.")

    def _copy_relay_setup(self) -> None:
        QGuiApplication.clipboard().setText(SETUP_COMMAND)
        self.status.setText("Команда скопирована: выполните её на сервере один раз (от root или через sudo).")

    def _check_public(self) -> None:
        """Asks the public address for /api/ping in the background: does the outside path reach this server?"""
        url = self.sharing.public_base() or ""
        self.status.setText(f"Проверяю {url} …")

        def go():
            import json
            import urllib.request
            try:
                req = urllib.request.Request(url + "/api/ping", headers={"User-Agent": "WallePlayer-check"})
                info = json.loads(urllib.request.urlopen(req, timeout=10).read())
                ok = info.get("app") == "WallePlayer"
                msg = ("Работает: по внешнему адресу отвечает этот сервер." if ok else
                       "По адресу отвечает что-то другое, не Walle Player.")
            except Exception as e:  # noqa: BLE001 - shown to the user
                msg = f"Не открывается: {e}. Проверьте проброс порта / настройки адреса (docs/internet-access.md)."
            QTimer.singleShot(0, lambda: self.status.setText(msg))
        threading.Thread(target=go, name="walle-public-check", daemon=True).start()

    def _add_user(self) -> None:
        name, ok = QInputDialog.getText(self, "Новый слушатель", "Имя (например, «Аня» или «Телефон в машине»):")
        if not ok or not name.strip():
            return
        u = self.sharing.add_user(name.strip())
        self._refresh()
        self.user.setCurrentIndex(self.user.findData(u["id"]))

    def _rename_user(self) -> None:
        u = self.selected_user()
        if not u:
            return
        name, ok = QInputDialog.getText(self, "Переименовать", "Имя слушателя:", text=u["name"])
        if ok and name.strip():
            self.sharing.rename_user(u["id"], name)

    def _del_user(self) -> None:
        u = self.selected_user()
        if not u or u.get("owner"):
            return
        if QMessageBox.question(self, "Удалить слушателя", f"«{u['name']}» потеряет доступ на всех своих "
                                "устройствах. Удалить?") == QMessageBox.StandardButton.Yes:
            self.sharing.remove_user(u["id"])

    def _copy(self) -> None:
        url = self.current_link()
        if url:
            QGuiApplication.clipboard().setText(url)
            self.status.setText("Ссылка скопирована. Отправьте её себе или слушателю — например, в мессенджере.")

    def _new_key(self) -> None:
        u = self.selected_user()
        if u and QMessageBox.question(self, "Сменить ключ", f"Устройства слушателя «{u['name']}» потеряют доступ, "
                                      "их нужно будет подключить заново по новому QR-коду. Сменить?") \
                == QMessageBox.StandardButton.Yes:
            self.sharing.new_key(u["id"])

    def _tunnel(self) -> None:
        t = self.sharing.tunnel
        if t is not None and t.running:
            self.sharing.stop_tunnel()
        elif find_cloudflared(self.sharing.ctx.home):
            self.sharing.start_tunnel()
        self._refresh()

    def done(self, r: int) -> None:
        self._timer.stop()
        try:
            self.sharing.changed.disconnect(self._refresh_soon)
        except (RuntimeError, TypeError):
            pass
        super().done(r)


def _quiet(fn, *args) -> None:
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 - closing a router port at exit is best effort
        log.debug("UPnP call failed", exc_info=True)
