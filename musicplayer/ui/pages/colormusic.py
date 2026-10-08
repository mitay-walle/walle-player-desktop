"""Colour music: the real spectrum of the playing track drawn as moving colour.

Modes: bars (spectrum), radial (ring around the cover), aurora (flowing glow), pulse (whole screen flashes on
beats).  Base hue comes from the current cover so the picture matches the album; without a cover colours
rotate with the music.  Idle (nothing playing) shows a slow breathing glow so the page is never blank.
"""

from __future__ import annotations

import colorsys
import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QConicalGradient, QImage, QLinearGradient, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from musicplayer.core.spectrum import SpectrumAnalyzer
from musicplayer.ui.context import AppContext
from musicplayer.ui.theme import TOKENS
from musicplayer.ui.widgets import JumpSlider

MODES = [("Спектр", "bars"), ("Кольцо", "radial"), ("Сияние", "aurora"), ("Пульс", "pulse")]


def dominant_hue(pm: QPixmap) -> tuple[float, float]:
    """(hue 0..1, saturation 0..1) of the most colourful part of a cover; (-1, 0) if it is grey."""
    if pm.isNull():
        return -1.0, 0.0
    img = pm.scaled(24, 24).toImage().convertToFormat(QImage.Format.Format_RGB32)
    best, weight = [0.0, 0.0], 0.0
    sx = sy = 0.0
    for y in range(img.height()):
        for x in range(img.width()):
            c = QColor(img.pixel(x, y))
            h, s, v, _ = c.getHsvF()
            w = s * v
            if h >= 0 and w > 0.05:
                sx += math.cos(h * 2 * math.pi) * w
                sy += math.sin(h * 2 * math.pi) * w
                best[1] += s * w
                weight += w
    if weight < 1.0:
        return -1.0, 0.0
    return (math.atan2(sy, sx) / (2 * math.pi)) % 1.0, min(1.0, best[1] / weight + 0.2)


class ColorMusic(QWidget):
    def __init__(self, ctx: AppContext, covers, strip: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.ctx, self.covers, self.strip = ctx, covers, strip
        self.an = SpectrumAnalyzer()
        self.mode = "bars"
        self.hue, self.sat = -1.0, 0.0
        self.cover_pm = QPixmap()
        self.phase = 0.0
        self._t0 = time.time()
        self.setMinimumHeight(10 if strip else 300)
        if strip:
            self.setFixedSize(110, 30)
        ctx.player.samples.connect(lambda m, sr: self.an.feed(m, sr))
        ctx.player.track_changed.connect(self.on_track)
        self.timer = QTimer(self, interval=33)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.on_track(ctx.player.current)

    def on_track(self, t: dict | None) -> None:
        self.cover_pm = self.covers.pixmap(t["cover_key"], 220) if t and t.get("cover_key") else QPixmap()
        self.hue, self.sat = dominant_hue(self.cover_pm)

    def _tick(self) -> None:
        if not self.isVisible():
            return
        self.an.step(self.ctx.player.state == "playing")
        self.phase += 0.002 + self.an.treble * 0.01
        self.update()

    def color(self, k: float, light: float = 0.55, alpha: float = 1.0) -> QColor:
        """Colour at position k (0..1) of the palette: around the cover hue, or a rainbow without one."""
        if self.hue >= 0:
            h = (self.hue + (k - 0.5) * 0.35 + self.phase * 0.2) % 1.0
        else:
            h = (k * 0.8 + self.phase) % 1.0
        r, g, b = colorsys.hls_to_rgb(h, light, max(0.55, self.sat) if self.hue >= 0 else 0.85)
        c = QColor.fromRgbF(r, g, b)
        c.setAlphaF(alpha)
        return c

    # ---- painting -----------------------------------------------------
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        an = self.an
        idle = not an.live and self.ctx.player.state != "playing"
        t = time.time() - self._t0
        bass = an.bass if not idle else 0.25 + 0.15 * math.sin(t * 0.8)
        bg = QColor(TOKENS["bg"])
        p.fillRect(self.rect(), bg)
        glow = QRadialGradient(QPointF(w / 2, h / 2), max(w, h) * (0.35 + 0.5 * bass))
        glow.setColorAt(0, self.color(0.5, 0.35, 0.28 + 0.5 * bass + 0.3 * an.beat))
        glow.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), QBrush(glow))
        if self.strip:
            self._bars(p, w, h, small=True)
        elif self.mode == "bars":
            self._bars(p, w, h)
        elif self.mode == "radial":
            self._radial(p, w, h, t)
        elif self.mode == "aurora":
            self._aurora(p, w, h, t)
        else:
            self._pulse(p, w, h)
        p.end()

    def _levels(self, idle: bool, t: float):
        if idle:
            n = self.an.n
            return [0.18 + 0.12 * math.sin(t * 1.2 + i * 0.5) for i in range(n)]
        return self.an.levels

    def _bars(self, p: QPainter, w: int, h: int, small: bool = False) -> None:
        lv = self._levels(not self.an.live and self.ctx.player.state != "playing", time.time() - self._t0)
        n = len(lv)
        gap = 2 if small else 4
        bw = (w - gap * (n + 1)) / n
        for i, v in enumerate(lv):
            bh = max(2.0, float(v) * (h - 6))
            x = gap + i * (bw + gap)
            grad = QLinearGradient(0, h, 0, h - bh)
            grad.setColorAt(0, self.color(i / n, 0.45, 0.95))
            grad.setColorAt(1, self.color(i / n + 0.12, 0.7, 0.95))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(QRectF(x, h - bh - 2, bw, bh), 2, 2)
            if not small:  # mirrored reflection
                p.setBrush(self.color(i / n, 0.5, 0.08))
                p.drawRect(QRectF(x, h - 2, bw, min(bh * 0.25, 18)))

    def _radial(self, p: QPainter, w: int, h: int, t: float) -> None:
        idle = not self.an.live and self.ctx.player.state != "playing"
        lv = self._levels(idle, t)
        n = len(lv)
        cx, cy = w / 2, h / 2
        r0 = min(w, h) * (0.17 + 0.03 * self.an.beat)
        for i, v in enumerate(lv):
            for sign in (1, -1):
                a = (i / n * math.pi * sign) - math.pi / 2 + self.phase * 0.6
                r1 = r0 + float(v) * min(w, h) * 0.32 + 4
                pen = QPen(self.color(i / n, 0.55, 0.95))
                pen.setWidthF(max(2.0, min(w, h) * 0.012))
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.drawLine(QPointF(cx + math.cos(a) * r0, cy + math.sin(a) * r0),
                           QPointF(cx + math.cos(a) * r1, cy + math.sin(a) * r1))
        if not self.cover_pm.isNull():
            d = r0 * 1.8
            from PySide6.QtGui import QPainterPath
            path = QPainterPath()
            path.addEllipse(QPointF(cx, cy), d / 2, d / 2)
            p.save()
            p.setClipPath(path)
            p.drawPixmap(QRectF(cx - d / 2, cy - d / 2, d, d), self.cover_pm, QRectF(self.cover_pm.rect()))
            p.restore()

    def _aurora(self, p: QPainter, w: int, h: int, t: float) -> None:
        a = self.an
        for layer, (lvl, k) in enumerate(((a.bass, 0.1), (a.mid, 0.5), (a.treble, 0.9))):
            amp = h * (0.10 + 0.32 * lvl)
            base = h * (0.35 + 0.15 * layer)
            from PySide6.QtGui import QPainterPath
            path = QPainterPath()
            path.moveTo(0, h)
            steps = 60
            for s in range(steps + 1):
                x = w * s / steps
                y = base + math.sin(x / w * 5 + t * (0.6 + layer * 0.4) + layer) * amp * 0.6 \
                    + math.sin(x / w * 11 - t * 0.9) * amp * 0.25
                path.lineTo(x, y)
            path.lineTo(w, h)
            path.closeSubpath()
            grad = QLinearGradient(0, base - amp, 0, h)
            grad.setColorAt(0, self.color(k, 0.6, 0.55 + 0.35 * lvl))
            grad.setColorAt(1, self.color(k + 0.2, 0.3, 0.0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(grad))
            p.drawPath(path)

    def _pulse(self, p: QPainter, w: int, h: int) -> None:
        a = self.an
        c = self.color(a.treble, 0.5, min(1.0, 0.15 + 0.85 * max(a.beat, a.bass * 0.6)))
        p.fillRect(self.rect(), c)
        ring = QRadialGradient(QPointF(w / 2, h / 2), min(w, h) * (0.2 + 0.5 * a.mid))
        ring.setColorAt(0, self.color(a.mid + 0.3, 0.75, 0.8 * (0.3 + a.mid)))
        ring.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(self.rect(), QBrush(ring))


class ColorMusicPage(QWidget):
    key = "colormusic"

    def __init__(self, ctx: AppContext, covers, parent=None) -> None:
        super().__init__(parent)
        self.view = ColorMusic(ctx, covers)
        self.mode = QComboBox()
        for label, _ in MODES:
            self.mode.addItem(label)
        self.sens = JumpSlider(Qt.Orientation.Horizontal, minimum=40, maximum=250, value=100)
        self.sens.setFixedWidth(180)
        self.info = QLabel()
        self.info.setStyleSheet(f"color:{TOKENS['text_muted']}")
        top = QHBoxLayout()
        top.addWidget(QLabel("Режим:"))
        top.addWidget(self.mode)
        top.addWidget(QLabel("Чувствительность:"))
        top.addWidget(self.sens)
        top.addWidget(self.info, 1)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.view, 1)
        self.mode.currentIndexChanged.connect(lambda i: setattr(self.view, "mode", MODES[i][1]))
        self.sens.valueChanged.connect(lambda v: setattr(self.view.an, "sensitivity", v / 100))
        self.t = QTimer(self, interval=500)
        self.t.timeout.connect(self._status)
        self.t.start()

    def set_mode(self, key: str) -> None:
        keys = [k for _, k in MODES]
        if key in keys:
            self.mode.setCurrentIndex(keys.index(key))

    def _status(self) -> None:
        a = self.view.an
        src = "живой звук" if a.live else "ожидание звука"
        self.info.setText(f"{src}   бас {a.bass:.2f}   середина {a.mid:.2f}   верха {a.treble:.2f}")
