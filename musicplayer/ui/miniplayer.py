"""Super-compact player: a small frameless always-on-top window (cover, title, transport, seek bar).

Drag it anywhere (the place is remembered at once), pin / unpin "always on top", double-click it or press the expand
button to return to the full window."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from musicplayer.ui.context import AppContext
from musicplayer.ui.icons import icon, make_icon_button
from musicplayer.ui.theme import TOKENS
from musicplayer.ui.widgets import CoverCache, JumpSlider, fmt_time

WIDTH, HEIGHT = 420, 104
PLAY = 36                                          # the round play button
COVER = 76


class MiniPlayer(QWidget):
    expand_requested = Signal()
    moved = Signal(QPoint)                         # dragged to a new place (the main window remembers it)

    def __init__(self, ctx: AppContext, covers: CoverCache) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        from musicplayer.ui.main_window import SeekSlider  # late import: main_window imports this module
        self.ctx, self.covers = ctx, covers
        self.setObjectName("mini")
        self.setStyleSheet(f"QWidget#mini {{ background:{TOKENS['surface']}; border:1px solid {TOKENS['border']}; }}")
        self.setFixedSize(WIDTH, HEIGHT)
        self.setToolTip("Мини-плеер: перетащите за пустое место; двойной клик — полное окно")
        self._drag: QPoint | None = None
        self._moved = False
        self._title = ""
        self._sub = ""

        self.cover = QLabel()
        self.cover.setFixedSize(COVER, COVER)
        self.cover.setToolTip("Двойной клик — вернуть полное окно")
        self.title = QLabel("Ничего не играет")                 # «Название — исполнитель» on one line
        self.title.setTextFormat(Qt.TextFormat.RichText)
        self.title.setStyleSheet(f"font-size:13px; color:{TOKENS['text']};")
        self.sub = self.title                                     # (one label for both, kept for older callers)
        self.time = QLabel("0:00")
        self.time.setStyleSheet(f"color:{TOKENS['text_dim']}; font-size:11px;")
        self.time.setToolTip("Прошло / длительность трека")

        self.btn_prev, self.btn_play, self.btn_next = QPushButton(), QPushButton(), QPushButton()
        make_icon_button(self.btn_prev, "prev", TOKENS["text"], 20, "Предыдущий трек")
        make_icon_button(self.btn_next, "next", TOKENS["text"], 20, "Следующий трек")
        for b in (self.btn_prev, self.btn_next):
            b.setFixedSize(32, 32)
        self.btn_like, self.btn_tags, self.btn_trash = QPushButton(), QPushButton(), QPushButton()
        make_icon_button(self.btn_like, "heart", TOKENS["text_muted"], 18, "В избранное")
        make_icon_button(self.btn_tags, "tag", TOKENS["text_muted"], 18, "Метки трека, альбома или исполнителя")
        make_icon_button(self.btn_trash, "trash", TOKENS["text_muted"], 18, "Удалить трек… (в корзину Windows, спросит)")
        for b in (self.btn_like, self.btn_tags, self.btn_trash):
            b.setFixedSize(30, 30)
        self.btn_vol = QPushButton()                              # click: a vertical volume slider pops up
        make_icon_button(self.btn_vol, "volume", TOKENS["text_muted"], 18, "")
        self.btn_vol.setFixedSize(30, 30)
        self.vol_popup = VolumePopup(ctx, self)
        self.btn_play.setFixedSize(PLAY, PLAY)
        self.btn_play.setIconSize(QSize(16, 16))
        self.btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play.setStyleSheet(f"QPushButton {{ background:{TOKENS['accent']}; border:none; border-radius:{PLAY // 2}px; "
                                    f"padding:0; min-height:{PLAY}px; max-height:{PLAY}px; min-width:{PLAY}px; max-width:{PLAY}px; }}"
                                    f"QPushButton:hover {{ background:{TOKENS.get('accent_hover', TOKENS['accent'])}; }}")

        self.btn_expand, self.btn_pin = QPushButton(), QPushButton()
        make_icon_button(self.btn_expand, "expand", TOKENS["text_muted"], 16, "Вернуть полное окно")
        make_icon_button(self.btn_pin, "pin", TOKENS["accent"], 16, "Поверх всех окон: включено (нажмите, чтобы выключить)")
        self.btn_pin.setCheckable(True)
        self.btn_pin.setChecked(True)
        for b in (self.btn_expand, self.btn_pin):
            b.setFixedSize(26, 24)
        self.seek = SeekSlider()
        self.seek.setFixedHeight(18)
        self.seek.setToolTip("Перемотка: клик или перетаскивание")

        head = QHBoxLayout()                                      # title — artist · expand · pin
        head.setSpacing(6)
        head.addWidget(self.title, 1)
        head.addWidget(self.btn_expand)
        head.addWidget(self.btn_pin)
        ctl = QHBoxLayout()
        ctl.setSpacing(10)
        ctl.addWidget(self.btn_prev)
        ctl.addWidget(self.btn_play)
        ctl.addWidget(self.btn_next)
        ctl.addSpacing(14)
        for b in (self.btn_like, self.btn_tags, self.btn_trash):
            ctl.addWidget(b)
        ctl.addSpacing(4)
        ctl.addWidget(self.btn_vol)
        ctl.addStretch(1)
        ctl.addWidget(self.time, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()                                       # right of the cover: three tight rows
        col.setSpacing(3)
        col.addLayout(head)
        col.addLayout(ctl)
        col.addWidget(self.seek)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 9, 12, 8)
        lay.setSpacing(12)
        lay.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(col, 1)

        p = ctx.player
        self.btn_prev.clicked.connect(p.prev)
        self.btn_next.clicked.connect(p.next)
        self.btn_play.clicked.connect(p.toggle)
        self.btn_like.clicked.connect(self.toggle_like)
        self.btn_tags.clicked.connect(self.labels_menu)
        self.btn_trash.clicked.connect(self.trash)
        self.btn_vol.clicked.connect(self.show_volume)
        self.vol_popup.changed.connect(self._show_volume_tip)
        self._show_volume_tip()
        ctx.events.favorites_changed.connect(lambda: self._show_like(p.current))
        self.btn_expand.clicked.connect(self.expand_requested)
        self.btn_pin.toggled.connect(self.set_on_top)
        self.seek.seek_requested.connect(lambda v: p.seek_fraction(v / 1000))
        p.track_changed.connect(self.on_track)
        p.state_changed.connect(self._show_state)
        p.position_changed.connect(self.on_position)
        self.on_track(p.current)
        self._show_state(p.state)

    # ---- content ------------------------------------------------------
    def _show_state(self, state: str) -> None:
        playing = state == "playing"
        self.btn_play.setIcon(icon("pause" if playing else "play", TOKENS["bg"], 16))
        self.btn_play.setToolTip("Пауза" if playing else "Играть")

    # ---- like / labels / delete for the playing track (as in the player bar) ------------------------------
    def _show_like(self, t: dict | None) -> None:
        on = bool(t) and self.ctx.is_liked("track", str(t["id"]))
        self.btn_like.setIcon(icon("heart_on" if on else "heart", TOKENS["accent"] if on else TOKENS["text_muted"], 18))
        who = f" — слушатель «{self.ctx.listener_name}»" if self.ctx.listener_name else ""
        self.btn_like.setToolTip(("Убрать из избранного" if on else "В избранное") + who)
        for b in (self.btn_like, self.btn_tags, self.btn_trash):
            b.setEnabled(t is not None)

    def toggle_like(self) -> None:
        t = self.ctx.player.current
        if t is not None:
            key = str(t["id"])
            self.ctx.set_liked("track", key, not self.ctx.is_liked("track", key))
            self.ctx.events.favorites_changed.emit()

    def labels_menu(self) -> None:
        from musicplayer.ui.tagpopup import TagPopup, labels_menu
        if not hasattr(self, "_tags"):
            self._tags = TagPopup(self.ctx, self)
        labels_menu(self.ctx, self.btn_tags, self._tags)

    # ---- volume ---------------------------------------------------------
    def show_volume(self) -> None:
        """The vertical slider right above the button (below it when the mini player sits at the top of the screen)."""
        pop = self.vol_popup
        pop.sync()
        pop.adjustSize()
        b = self.btn_vol
        above = b.mapToGlobal(QPoint((b.width() - pop.width()) // 2, -pop.height() - 4))
        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is not None and above.y() < screen.top():
            above.setY(b.mapToGlobal(QPoint(0, b.height() + 4)).y())
        pop.move(above)
        pop.show()
        pop.slider.setFocus()

    def _show_volume_tip(self) -> None:
        self.btn_vol.setToolTip(f"Громкость: {round(self.ctx.player.volume * 100)}% (нажмите — ползунок, колесо — громче/тише)")

    def showEvent(self, e) -> None:
        self._show_volume_tip()                                # the volume may have changed in the full window
        super().showEvent(e)

    def wheelEvent(self, e) -> None:
        if self.btn_vol.underMouse():                          # the wheel over the volume button changes the volume
            self.vol_popup.step(5 if e.angleDelta().y() > 0 else -5)
            e.accept()
        else:
            super().wheelEvent(e)

    def trash(self) -> None:
        from musicplayer.ui.pages.library import confirm_trash
        t = self.ctx.player.current
        if t is not None:
            confirm_trash(self, self.ctx, [t])

    def _show_title(self) -> None:
        """Bold title and the muted artist on one line, shortened with «…» to the room there is."""
        from html import escape
        room = WIDTH - COVER - 22 - 12 - 2 * 26 - 12
        fm = QFontMetrics(self.title.font())
        title = fm.elidedText(self._title, Qt.TextElideMode.ElideRight, int(room * (0.62 if self._sub else 1)))
        rest = room - fm.horizontalAdvance(title + " — ")
        sub = fm.elidedText(self._sub, Qt.TextElideMode.ElideRight, max(rest, 0)) if self._sub else ""
        self.title.setText(f"<b>{escape(title)}</b>" + (f"<span style='color:{TOKENS['text_muted']}'> — {escape(sub)}</span>"
                                                         if sub else ""))

    def on_track(self, t: dict | None) -> None:
        self._title = t["title"] if t else "Ничего не играет"
        self._sub = f"{t['artist']}" + (f" feat. {', '.join(t['feat'])}" if t and t.get("feat") else "") if t else ""
        self._show_title()
        full = f"{self._title} — {self._sub}" if self._sub else self._title
        self.title.setToolTip(full)
        self.cover.setPixmap(self.covers.pixmap(t["cover_key"] if t else None, COVER))
        self.seek.setValue(0)
        self.time.setText("0:00")
        self._show_like(t)

    def on_position(self, pos: float, dur: float) -> None:
        self.time.setText(f"{fmt_time(pos)} / {fmt_time(dur)}")
        if not self.seek.dragging and dur > 0:
            self.seek.setValue(int(pos / dur * 1000))

    def set_on_top(self, on: bool) -> None:
        geo = self.pos()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(on))
        self.btn_pin.setIcon(icon("pin", TOKENS["accent"] if on else TOKENS["text_dim"], 16))
        self.btn_pin.setToolTip("Поверх всех окон: включено (нажмите, чтобы выключить)" if on
                                else "Поверх всех окон: выключено (нажмите, чтобы включить)")
        self.show()                                   # changing a window flag hides the window: show it again
        self.move(geo)

    # ---- dragging the frameless window -------------------------------------
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._moved = False

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)
            self._moved = True

    def mouseReleaseEvent(self, e) -> None:
        self._drag = None
        if self._moved:
            self._moved = False
            self.moved.emit(self.pos())

    def mouseDoubleClickEvent(self, e) -> None:
        self.expand_requested.emit()


class VolumePopup(QFrame):
    """A small popup with a vertical volume slider and the percentage; closes on a click elsewhere or Escape."""

    changed = Signal()

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.ctx = ctx
        self.setObjectName("volumePopup")
        self.setStyleSheet(f"#volumePopup {{ background:{TOKENS['surface']}; "
                           f"border:1px solid {TOKENS['border']}; border-radius:8px; }}")
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet(f"color:{TOKENS['text_muted']}; font-size:11px; border:none;")
        self.slider = JumpSlider(Qt.Orientation.Vertical)
        self.slider.setRange(0, 100)
        self.slider.setFixedSize(32, 130)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.slider.setToolTip("Громкость")
        self.slider.setStyleSheet(                                # as the horizontal ones: filled from the bottom, a round knob
            f"QSlider::groove:vertical {{ width:4px; background:{TOKENS['surface2']}; border-radius:2px; }}"
            f"QSlider::add-page:vertical {{ background:{TOKENS['accent']}; border-radius:2px; }}"
            f"QSlider::sub-page:vertical {{ background:{TOKENS['surface2']}; border-radius:2px; }}"
            f"QSlider::handle:vertical {{ width:14px; height:14px; margin:0 -5px; background:{TOKENS['text']}; "
            f"border:0; border-radius:7px; }}"
            f"QSlider::handle:vertical:hover {{ background:#ffffff; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 8, 6, 6)
        lay.setSpacing(4)
        lay.addWidget(self.slider, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(self.label)
        self.setFixedWidth(48)
        self.slider.valueChanged.connect(self._set)
        self.sync()

    def sync(self) -> None:
        v = round(self.ctx.player.volume * 100)
        self.slider.blockSignals(True)
        self.slider.setValue(v)
        self.slider.blockSignals(False)
        self.label.setText(f"{v}%")

    def step(self, d: int) -> None:
        self.sync()
        self.slider.setValue(self.slider.value() + d)

    def _set(self, v: int) -> None:
        self.ctx.player.set_volume(v / 100)
        self.label.setText(f"{v}%")
        self.changed.emit()
