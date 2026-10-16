"""Crisp vector icons (design kit style: thin rounded strokes, no button background).

Every icon is a small SVG drawn into a QIcon at 3x resolution, so it stays sharp at any zoom and DPI."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap

_S = 'stroke="{c}" stroke-width="1.8" fill="none" stroke-linecap="round" stroke-linejoin="round"'
SVG = {
    "play": '<path d="M8 5.5v13l10.5-6.5z" fill="{c}" stroke="{c}" stroke-width="1.2" stroke-linejoin="round"/>',
    "pause": '<rect x="6.5" y="5" width="3.6" height="14" rx="1.2" fill="{c}"/><rect x="13.9" y="5" width="3.6" height="14" rx="1.2" fill="{c}"/>',
    "prev": f'<path d="M6 5v14" {_S}/><path d="M19 5.5v13L9 12z" {_S}/>',
    "next": f'<path d="M18 5v14" {_S}/><path d="M5 5.5v13L15 12z" {_S}/>',
    "shuffle": f'<path d="M3 7h3.5c4 0 6 10 10 10H21" {_S}/><path d="M3 17h3.5c1.6 0 2.8-1.6 3.8-3.6M13.7 9.6C14.7 8 15.6 7 17 7h4" {_S}/>'
               f'<path d="M18.5 4.5L21 7l-2.5 2.5M18.5 14.5L21 17l-2.5 2.5" {_S}/>',
    "repeat": f'<path d="M4 11V9a2 2 0 0 1 2-2h13" {_S}/><path d="M16.5 4.5L19 7l-2.5 2.5" {_S}/>'
              f'<path d="M20 13v2a2 2 0 0 1-2 2H5" {_S}/><path d="M7.5 19.5L5 17l2.5-2.5" {_S}/>',
    "repeat_one": f'<path d="M4 11V9a2 2 0 0 1 2-2h13" {_S}/><path d="M16.5 4.5L19 7l-2.5 2.5" {_S}/>'
                  f'<path d="M20 13v2a2 2 0 0 1-2 2H5" {_S}/><path d="M7.5 19.5L5 17l2.5-2.5" {_S}/>'
                  f'<path d="M11.2 10.6l1.3-.9v5" {_S}/>',
    "heart": f'<path d="M12 20s-7.5-4.6-7.5-10A4.3 4.3 0 0 1 12 7.4 4.3 4.3 0 0 1 19.5 10C19.5 15.4 12 20 12 20z" {_S}/>',
    "heart_on": '<path d="M12 20s-7.5-4.6-7.5-10A4.3 4.3 0 0 1 12 7.4 4.3 4.3 0 0 1 19.5 10C19.5 15.4 12 20 12 20z" '
                'fill="{c}" stroke="{c}" stroke-width="1.8" stroke-linejoin="round"/>',
    "volume": f'<path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" {_S}/><path d="M15.5 9a4.2 4.2 0 0 1 0 6M18 6.5a7.8 7.8 0 0 1 0 11" {_S}/>',
    "queue": f'<path d="M4 6.5h16M4 12h16M4 17.5h10" {_S}/>',
    "mini": f'<rect x="3.5" y="5" width="17" height="14" rx="2.5" {_S}/><rect x="11.5" y="12" width="7" height="5" rx="1" fill="{{c}}"/>',
    "compact": f'<path d="M4 9h16M4 15h16" {_S}/><rect x="3.5" y="5" width="17" height="14" rx="2.5" {_S}/>',
    "tag": f'<path d="M3.5 12.5V5a1.5 1.5 0 0 1 1.5-1.5h7.5l8 8-9 9z" {_S}/><circle cx="8" cy="8" r="1.4" fill="{{c}}"/>',
    "thumb_up": f'<path d="M7.5 10.5V20H4.5v-9.5zM7.5 10.5l3.6-6.3a1.6 1.6 0 0 1 2.9 1.1L13.3 9.5h5a1.8 1.8 0 0 1 1.8 2.1l-1.3 6.7A2 2 0 0 1 16.8 20H7.5" {_S}/>',
    "thumb_down": f'<path d="M7.5 13.5V4H4.5v9.5zM7.5 13.5l3.6 6.3a1.6 1.6 0 0 0 2.9-1.1l-.7-4.2h5a1.8 1.8 0 0 0 1.8-2.1l-1.3-6.7A2 2 0 0 0 16.8 4H7.5" {_S}/>',
    "edit": f'<path d="M4 20h4L19.3 8.7a2 2 0 0 0 0-2.8l-1.2-1.2a2 2 0 0 0-2.8 0L4 16z" {_S}/>',
    "pin": f'<path d="M9 4h6l-1 6 3 3H7l3-3zM12 13v7" {_S}/>',
    "trash": f'<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 12.5h9l1-12.5M10.5 11v5.5M13.5 11v5.5" {_S}/>',
    "hide": f'<path d="M3.5 12s3-6 8.5-6c1.6 0 3 .5 4.2 1.2M20.5 12s-3 6-8.5 6c-1.6 0-3-.5-4.2-1.2" {_S}/><path d="M10 12a2 2 0 0 0 3.1 1.7M14 11.2A2 2 0 0 0 12.8 10" {_S}/><path d="M4.5 19.5l15-15" {_S}/>',
    "mic": f'<rect x="9" y="3.5" width="6" height="10.5" rx="3" {_S}/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" {_S}/>',
    "album": f'<circle cx="12" cy="12" r="8.5" {_S}/><circle cx="12" cy="12" r="2.2" {_S}/>',
    "list": f'<path d="M9 6.5h11M9 12h11M9 17.5h11" {_S}/><circle cx="4.8" cy="6.5" r="1" fill="{{c}}"/><circle cx="4.8" cy="12" r="1" fill="{{c}}"/><circle cx="4.8" cy="17.5" r="1" fill="{{c}}"/>',
    "clock": f'<circle cx="12" cy="12" r="8.5" {_S}/><path d="M12 7.5V12l3 2" {_S}/>',
    "chart": f'<path d="M4 19.5h16" {_S}/><path d="M7 16v-5M12 16V6.5M17 16v-8" {_S}/>',
    "spark": f'<path d="M12 3.5v4M12 16.5v4M3.5 12h4M16.5 12h4M6 6l2.8 2.8M15.2 15.2L18 18M18 6l-2.8 2.8M8.8 15.2L6 18" {_S}/>',
    "bars": f'<rect x="4" y="12" width="3.5" height="8" rx="1" {_S}/><rect x="10.25" y="7" width="3.5" height="13" rx="1" {_S}/><rect x="16.5" y="4" width="3.5" height="16" rx="1" {_S}/>',
    "folder": f'<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2.5h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" {_S}/>',
    "refresh": f'<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3" {_S}/><path d="M19.5 4.5v4h-4" {_S}/>',
    "expand": f'<path d="M14 4h6v6M10 20H4v-6M20 4l-7 7M4 20l7-7" {_S}/>',
    "phone": f'<rect x="7" y="3" width="10" height="18" rx="2.2" {_S}/><path d="M11 17.5h2" {_S}/>',
}

_cache: dict = {}


def icon(name: str, color: str = "#E6E3DA", size: int = 22) -> QIcon:
    """The named icon in ``color`` (rendered at 3x for HiDPI)."""
    key = (name, color, size)
    if key not in _cache:
        from PySide6.QtSvg import QSvgRenderer
        body = SVG[name].replace("{c}", color).replace("{{c}}", color)
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{body}</svg>'
        px = size * 3
        img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(p, QRectF(0, 0, px, px))
        p.end()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(3.0)
        _cache[key] = QIcon(pm)
    return _cache[key]


ICON_BUTTON_CSS = ("QPushButton { background:transparent; border:none; padding:4px; }"
                   "QPushButton:hover { background:rgba(230,227,218,18); border-radius:8px; }"
                   "QPushButton:disabled { background:transparent; }")


def make_icon_button(btn, name: str, color: str = "#E6E3DA", size: int = 22, tip: str = "") -> None:
    """Turn a QPushButton into a background-less icon button (keeps its signals, size hints and shortcuts)."""
    btn.setText("")
    btn.setIcon(icon(name, color, size))
    btn.setIconSize(QSize(size, size))
    btn.setFlat(True)
    btn.setStyleSheet(ICON_BUTTON_CSS)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    if tip:
        btn.setToolTip(tip)
