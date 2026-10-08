"""Super-compact player: a small frameless always-on-top window (cover, title, transport, seek bar).

Drag it anywhere, pin / unpin "always on top", double-click the cover or press ⤢ to return to the full window."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from musicplayer.ui.context import AppContext
from musicplayer.ui.theme import TOKENS
from musicplayer.ui.widgets import CoverCache, fmt_time

WIDTH, HEIGHT = 380, 96


class MiniPlayer(QWidget):
    expand_requested = Signal()

    def __init__(self, ctx: AppContext, covers: CoverCache) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        from musicplayer.ui.main_window import SeekSlider  # late import: main_window imports this module
        self.ctx, self.covers = ctx, covers
        self.setObjectName("mini")
        self.setStyleSheet(f"QWidget#mini {{ background:{TOKENS['surface']}; border:1px solid {TOKENS['border']}; }}")
        self.setFixedSize(WIDTH, HEIGHT)
        self._drag: QPoint | None = None
        self._title = ""
        self._sub = ""

        self.cover = QLabel()
        self.cover.setFixedSize(56, 56)
        self.title = QLabel("Ничего не играет")
        self.title.setStyleSheet("font-weight:600; font-size:12px;")
        self.sub = QLabel()
        self.sub.setStyleSheet(f"color:{TOKENS['text_muted']}; font-size:11px;")
        self.time = QLabel("0:00")
        self.time.setStyleSheet(f"color:{TOKENS['text_dim']}; font-size:10px;")
        self.btn_prev, self.btn_play, self.btn_next = QPushButton("◀◀"), QPushButton("▶"), QPushButton("▶▶")
        for b in (self.btn_prev, self.btn_play, self.btn_next):
            b.setFixedSize(34, 28)
            b.setFlat(True)
        self.btn_pin = QPushButton("◆")
        self.btn_pin.setCheckable(True)
        self.btn_pin.setChecked(True)
        self.btn_pin.setToolTip("Поверх всех окон")
        self.btn_expand = QPushButton("⤢")
        self.btn_expand.setToolTip("Вернуть полное окно")
        for b in (self.btn_pin, self.btn_expand):
            b.setFixedSize(26, 22)
            b.setFlat(True)
        self.seek = SeekSlider()
        self.seek.setFixedHeight(20)

        texts = QVBoxLayout()
        texts.setSpacing(0)
        texts.addWidget(self.title)
        texts.addWidget(self.sub)
        ctl = QHBoxLayout()
        ctl.setSpacing(4)
        for b in (self.btn_prev, self.btn_play, self.btn_next):
            ctl.addWidget(b)
        ctl.addStretch(1)
        ctl.addWidget(self.time)
        top = QHBoxLayout()
        top.addWidget(self.cover)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addLayout(texts)
        col.addLayout(ctl)
        top.addLayout(col, 1)
        side = QVBoxLayout()
        side.addWidget(self.btn_expand)
        side.addWidget(self.btn_pin)
        side.addStretch(1)
        top.addLayout(side)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 6, 4)
        lay.setSpacing(2)
        lay.addLayout(top, 1)
        lay.addWidget(self.seek)

        p = ctx.player
        self.btn_prev.clicked.connect(p.prev)
        self.btn_next.clicked.connect(p.next)
        self.btn_play.clicked.connect(p.toggle)
        self.btn_expand.clicked.connect(self.expand_requested)
        self.btn_pin.toggled.connect(self.set_on_top)
        self.seek.seek_requested.connect(lambda v: p.seek_fraction(v / 1000))
        p.track_changed.connect(self.on_track)
        p.state_changed.connect(lambda s: self.btn_play.setText("❚❚" if s == "playing" else "▶"))
        p.position_changed.connect(self.on_position)
        self.on_track(p.current)
        self.btn_play.setText("❚❚" if p.state == "playing" else "▶")

    # ---- content ------------------------------------------------------
    def _elide(self, label: QLabel, text: str) -> None:
        label.setText(QFontMetrics(label.font()).elidedText(text, Qt.TextElideMode.ElideRight, WIDTH - 56 - 26 - 40))

    def on_track(self, t: dict | None) -> None:
        self._title = t["title"] if t else "Ничего не играет"
        self._sub = f"{t['artist']}" + (f" feat. {', '.join(t['feat'])}" if t and t.get("feat") else "") if t else ""
        self._elide(self.title, self._title)
        self._elide(self.sub, self._sub)
        self.title.setToolTip(f"{self._title} — {self._sub}")
        self.cover.setPixmap(self.covers.pixmap(t["cover_key"] if t else None, 56))
        self.seek.setValue(0)
        self.time.setText("0:00")

    def on_position(self, pos: float, dur: float) -> None:
        self.time.setText(f"{fmt_time(pos)} / {fmt_time(dur)}")
        if not self.seek.dragging and dur > 0:
            self.seek.setValue(int(pos / dur * 1000))

    def set_on_top(self, on: bool) -> None:
        geo = self.pos()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(on))
        self.show()                                   # changing a window flag hides the window: show it again
        self.move(geo)

    # ---- dragging the frameless window -------------------------------------
    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e) -> None:
        self._drag = None

    def mouseDoubleClickEvent(self, e) -> None:
        self.expand_requested.emit()
