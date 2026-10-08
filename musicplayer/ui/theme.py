"""Design tokens, Qt stylesheet and palette for the dark "dark"-like theme.

Every colour used by the UI lives in :data:`TOKENS`; :func:`build_qss` turns the
tokens into a stylesheet and :func:`apply_theme` installs the Fusion style, a
full dark :class:`QPalette` and the stylesheet on a :class:`QApplication`.

QSS has no colour functions, so hover/pressed/selection variants are
precomputed here (see :func:`derived_colors`).
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPalette
from PySide6.QtWidgets import QApplication

logger = logging.getLogger(__name__)

TOKENS: dict[str, Any] = {          # WallePlayer design kit: Carbon #111113, Ochre #D5A52C, Warm white #E6E3DA
    "bg": "#111113",  # window canvas (Carbon)
    "surface": "#18181b",  # cards / nav
    "surface2": "#222226",  # hover / inputs / table rows alt
    "border": "#2c2c31",
    "accent": "#D5A52C",  # Ochre: primary buttons, active items, progress
    "accent2": "#E6E3DA",  # Warm white
    "success": "#8FC77E",
    "warning": "#E8B04A",
    "danger": "#E2735F",
    "text": "#E6E3DA",
    "text_muted": "#92928F",
    "text_dim": "#5F5F63",
    "chart": [
        "#D5A52C", "#E6E3DA", "#8FC77E", "#E2735F", "#7FA7C9",
        "#B98ACB", "#E8B04A", "#6FBFAE", "#C9A27A", "#92928F",
    ],
    "radius": 8,
    "radius_lg": 12,
    "font": "Segoe UI",
    "font_size": 10,
}

# Precomputed hover value for ``surface2`` (QSS cannot lighten colours); documented in SPEC §4.1.
SURFACE2_HOVER = "#2a2a2f"


def color_tokens(tokens: dict[str, Any] = TOKENS) -> dict[str, str]:
    """Return only the scalar colour tokens (``"#rrggbb"`` strings) of ``tokens``."""
    return {k: v for k, v in tokens.items() if isinstance(v, str) and v.startswith("#")}


def rgba(color: str, alpha: float) -> str:
    """Return a QSS ``rgba(r,g,b,a%)`` string for a ``#rrggbb`` colour."""
    c = QColor(color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{int(round(alpha * 100))}%)"


def derived_colors(tokens: dict[str, Any] = TOKENS) -> dict[str, str]:
    """Precompute hover / pressed / selection colours that QSS cannot express."""
    accent = QColor(tokens["accent"])
    return {
        "accent_hover": accent.lighter(108).name(),
        "accent_pressed": accent.darker(110).name(),
        "surface2_hover": SURFACE2_HOVER,
        "selection": rgba(tokens["accent"], 0.35),
        "selection_hover": rgba(tokens["accent"], 0.45),
        "accent_soft": rgba(tokens["accent"], 0.18),
        "success_soft": rgba(tokens["success"], 0.16),
        "warning_soft": rgba(tokens["warning"], 0.16),
        "danger_soft": rgba(tokens["danger"], 0.16),
        "info_soft": rgba(tokens["accent"], 0.16),
    }


# --------------------------------------------------------------------------- stylesheet images
# QSS cannot draw glyphs: the old "CSS border triangle" trick renders as a flat bar in Qt, and a
# checked QCheckBox showed a plain blue square.  These tiny SVGs are rasterised into PNG files
# (see :func:`ensure_theme_images`) and referenced with ``image: url(...)``.
_IMAGE_SVGS: dict[str, str] = {
    "chevron_down": '<path d="M6 9l6 6 6-6" stroke="{color}" stroke-width="2.4" fill="none" '
                    'stroke-linecap="round" stroke-linejoin="round"/>',
    "chevron_up": '<path d="M6 15l6-6 6 6" stroke="{color}" stroke-width="2.4" fill="none" '
                  'stroke-linecap="round" stroke-linejoin="round"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5" stroke="{color}" stroke-width="3" fill="none" '
             'stroke-linecap="round" stroke-linejoin="round"/>',
    "radio_dot": '<circle cx="12" cy="12" r="6.5" fill="{color}"/>',
}
#: image name -> token used for its colour
_IMAGE_COLORS = {"chevron_down": "text_muted", "chevron_up": "text_muted", "check": "bg", "radio_dot": "accent"}
_IMAGE_PX = 32  # rendered size; QSS scales the image down to the sub-control


def theme_image_dir(tokens: dict[str, Any] = TOKENS) -> Path:
    """Per-token-set folder (under the system temp dir) that holds the stylesheet PNGs."""
    key = "|".join(f"{name}={tokens.get(_IMAGE_COLORS[name], '')}" for name in sorted(_IMAGE_SVGS))
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    return Path(tempfile.gettempdir()) / f"musicplayer_theme_{digest}"


def theme_image_paths(tokens: dict[str, Any] = TOKENS) -> dict[str, str]:
    """``{name: "C:/.../name.png"}`` with forward slashes (as QSS ``url()`` expects)."""
    folder = theme_image_dir(tokens)
    return {name: (folder / f"{name}.png").as_posix() for name in _IMAGE_SVGS}


def ensure_theme_images(tokens: dict[str, Any] = TOKENS) -> dict[str, str]:
    """Render the stylesheet PNGs once (needs a QGuiApplication); returns :func:`theme_image_paths`."""
    from PySide6.QtSvg import QSvgRenderer

    paths = theme_image_paths(tokens)
    folder = theme_image_dir(tokens)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("cannot create %s: %s", folder, exc)
        return paths
    for name, body in _IMAGE_SVGS.items():
        target = Path(paths[name])
        if target.is_file() and target.stat().st_size > 0:
            continue
        color = str(tokens.get(_IMAGE_COLORS[name], "#ffffff"))
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{body.format(color=color)}</svg>'
        renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        image = QImage(_IMAGE_PX, _IMAGE_PX, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter, QRectF(0, 0, _IMAGE_PX, _IMAGE_PX))
        painter.end()
        tmp = target.with_name(f"{target.stem}.{os.getpid()}.tmp.png")
        try:
            if image.save(str(tmp), "PNG"):
                os.replace(tmp, target)
        except OSError as exc:
            logger.warning("cannot write theme image %s: %s", target, exc)
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
    return paths


def build_qss(tokens: dict[str, Any] = TOKENS) -> str:
    """Build the application stylesheet from ``tokens``.

    The result contains no template leftovers; every colour token appears at
    least once (chart colours are listed in a trailing comment so that the
    palette is discoverable from the stylesheet as well).
    """
    t = dict(tokens)
    d = derived_colors(tokens)
    font = t["font"]
    fs = int(t["font_size"])
    r = int(t["radius"])
    rl = int(t["radius_lg"])
    bg, surface, surface2, border = t["bg"], t["surface"], t["surface2"], t["border"]
    accent, accent2 = t["accent"], t["accent2"]
    success, warning, danger = t["success"], t["warning"], t["danger"]
    text, muted, dim = t["text"], t["text_muted"], t["text_dim"]
    chart_comment = ", ".join(t.get("chart", []))
    img = theme_image_paths(tokens)

    qss = f"""
/* ---- base ---- */
QWidget {{
    color: {text};
    font-family: "{font}";
    font-size: {fs}pt;
    selection-background-color: {d['selection']};
    selection-color: {text};
    outline: 0;
}}
QMainWindow, QDialog, QWidget#Page, QStackedWidget, QWidget#Root {{ background: {bg}; }}
QWidget:disabled {{ color: {dim}; }}
QLabel {{ background: transparent; }}
QLabel#Muted {{ color: {muted}; }}
QLabel#Dim {{ color: {dim}; }}
QLabel#Title {{ font-size: 13pt; font-weight: 600; color: {text}; }}
QLabel#H1 {{ font-size: 16pt; font-weight: 600; color: {text}; }}
QLabel#StatValue {{ font-size: 20pt; font-weight: 600; color: {text}; }}
QLabel#StatValueSmall {{ font-size: 14pt; font-weight: 600; color: {text}; }}
QLabel#StatTitle {{ font-size: 9pt; color: {muted}; }}
QLabel#Brand {{ font-size: 13pt; font-weight: 700; color: {text}; }}
QLabel#Badge {{ border-radius: 0; padding: 2px 8px; background: {surface2}; color: {muted}; font-size: 9pt; }}
QToolButton#Chip {{ border: 0; border-radius: 0; padding: 2px 10px; background: {surface2}; color: {muted}; font-size: 9pt; }}
QToolButton#Chip:hover {{ color: {text}; }}
QToolButton#Chip:checked {{ background: {accent}; color: {bg}; }}
QLabel#NavBadge {{ border-radius: 0; padding: 0px 5px; background: {accent}; color: {bg}; font-size: 8pt; font-weight: 600; }}
QLabel#Accent {{ color: {accent}; }}
QLabel#Accent2 {{ color: {accent2}; }}
QLabel#Success {{ color: {success}; }}
QLabel#Warning {{ color: {warning}; }}
QLabel#Danger {{ color: {danger}; }}

/* ---- cards ---- */
QFrame#Card {{ background: {surface}; border: 0; border-radius: {rl}px; }}
QWidget#NavBar {{ background: {surface}; border: 0; }}
QWidget#IconTile {{ background: {d['accent_soft']}; border-radius: 0; }}

/* ---- notices ---- */
QFrame#Notice {{ background: {surface2}; border-radius: 0; border-left: 3px solid {accent}; }}
QFrame#Notice QPushButton {{ background: {surface}; }}
QFrame#Notice QPushButton:hover {{ background: {d['surface2_hover']}; }}
QFrame#Notice[kind="info"] {{ border-left: 3px solid {accent}; background: {d['info_soft']}; }}
QFrame#Notice[kind="success"] {{ border-left: 3px solid {success}; background: {d['success_soft']}; }}
QFrame#Notice[kind="warning"] {{ border-left: 3px solid {warning}; background: {d['warning_soft']}; }}
QFrame#Notice[kind="danger"] {{ border-left: 3px solid {danger}; background: {d['danger_soft']}; }}
QLabel#NoticeTitle {{ font-weight: 600; }}
QLabel#NoticeText {{ color: {muted}; }}
QToolButton#NoticeClose {{ border: 0; background: transparent; padding: 2px; border-radius: 0; }}
QToolButton#NoticeClose:hover {{ background: {d['surface2_hover']}; }}

/* ---- buttons ---- */
QPushButton {{
    background: {surface2};
    color: {text};
    border: 0;
    border-radius: {r}px;
    padding: 6px 14px;
    min-height: 18px;
}}
QPushButton:hover {{ background: {d['surface2_hover']}; }}
QPushButton:pressed {{ background: {border}; }}
QPushButton:disabled {{ background: {surface2}; color: {dim}; }}
QPushButton:checked {{ background: {accent}; color: {bg}; }}
QPushButton#Primary {{ background: {accent}; color: {bg}; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {d['accent_hover']}; }}
QPushButton#Primary:pressed {{ background: {d['accent_pressed']}; }}
QPushButton#Primary:disabled {{ background: {surface2}; color: {dim}; }}
QPushButton#Danger {{ background: {danger}; color: {text}; }}
QPushButton#Danger:hover {{ background: {danger}; }}
QPushButton#Flat {{ background: transparent; }}
QPushButton#Flat:hover {{ background: {surface2}; }}
QToolButton {{ background: transparent; border: 0; border-radius: 0; padding: 4px; color: {text}; }}
QToolButton:hover {{ background: {d['surface2_hover']}; }}
QToolButton:pressed {{ background: {border}; }}
QToolButton:checked {{ background: {d['accent_soft']}; }}
QToolButton:disabled {{ color: {dim}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

/* ---- nav ---- */
QToolButton#NavItem {{
    border: 0;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    padding: 10px 14px;
    color: {muted};
    background: transparent;
}}
QToolButton#NavItem[density="cozy"] {{ padding: 10px 10px; }}
QToolButton#NavItem[density="compact"] {{ padding: 10px 6px; }}
QToolButton#NavItem:hover {{ color: {text}; background: transparent; }}
QToolButton#NavItem:checked {{ background: transparent; }}
QToolButton#NavItem[active="true"] {{ border-bottom: 2px solid {accent}; color: {text}; }}
QToolButton#LangButton {{ padding: 3px 7px; font-size: 9pt; font-weight: 600; color: {muted}; border-radius: 0; }}
QToolButton#LangButton:checked {{ background: {surface2}; color: {text}; }}
QToolButton#BellButton {{ padding: 5px; border-radius: 0; }}
QToolButton#IconButton {{ padding: 5px; border-radius: 0; }}

/* ---- inputs ---- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QPlainTextEdit {{
    background: {surface2};
    color: {text};
    border: 1px solid {border};
    border-radius: {r}px;
    padding: 6px 8px;
    selection-background-color: {d['selection']};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {accent};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {dim}; }}
QLineEdit[readOnly="true"] {{ color: {muted}; }}
QComboBox::drop-down {{ border: 0; width: 22px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url("{img['chevron_down']}"); width: 12px; height: 12px; margin-right: 8px; }}
QComboBox::down-arrow:disabled {{ image: none; }}
QComboBox QAbstractItemView {{
    background: {surface2};
    color: {text};
    selection-background-color: {d['selection']};
    selection-color: {text};
    border: 1px solid {border};
    border-radius: 0;
    padding: 4px;
    outline: 0;
}}
QComboBox QAbstractItemView::item {{ padding: 5px 8px; min-height: 22px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::down-button {{
    border: 0; background: transparent; width: 16px;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{img['chevron_up']}"); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{img['chevron_down']}"); width: 10px; height: 10px; }}
QPlainTextEdit, QTextEdit {{ padding: 6px; }}
QPlainTextEdit#Console {{ font-family: "Consolas", "Cascadia Mono", monospace; font-size: 9pt; background: {bg}; }}

/* ---- check / radio ---- */
QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {border};
    background: {surface2};
}}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border: 1px solid {muted}; }}
QCheckBox::indicator:checked {{ background: {accent}; border: 1px solid {accent}; image: url("{img['check']}"); }}
QRadioButton::indicator:checked {{ background: {surface2}; border: 1px solid {accent}; image: url("{img['radio_dot']}"); }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ background: {surface}; border: 1px solid {border}; }}

/* ---- slider ---- */
QSlider {{ background: transparent; }}
QSlider::groove:horizontal {{ height: 4px; background: {surface2}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
QSlider::add-page:horizontal {{ background: {surface2}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 14px; height: 14px; margin: -5px 0;
    background: {text}; border: 0; border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ background: #ffffff; }}
QSlider::groove:vertical {{ width: 4px; background: {surface2}; border-radius: 0; }}
QSlider::handle:vertical {{ width: 14px; height: 14px; margin: 0 -5px; background: {accent}; border-radius: 0; }}

/* ---- progress ---- */
QProgressBar {{ height: 6px; max-height: 6px; border: 0; border-radius: 3px; background: {surface2}; text-align: center; color: transparent; }}
QProgressBar::chunk {{ border-radius: 3px; background: {accent}; }}

/* ---- tables ---- */
QTableView, QTreeView, QListView {{
    background: {surface};
    alternate-background-color: {surface2};
    selection-background-color: {d['selection']};
    selection-color: {text};
    gridline-color: transparent;
    border: 0;
    border-radius: 0;
    outline: 0;
}}
QTableView::item, QTreeView::item, QListView::item {{ padding: 4px 6px; border: 0; }}
/* Selected rows: the translucent accent comes from ``selection-background-color`` above (painted once by
   the base style).  A ``background`` on ``::item:selected`` would be composited again by the stylesheet
   style and turn the 35% accent into a near-solid blue, so the rule only fixes the text colour. */
QTableView::item:selected, QTreeView::item:selected, QListView::item:selected {{ color: {text}; }}
QTableView::item:hover, QListView::item:hover {{ background: {d['surface2_hover']}; }}
QTableView::item:selected:hover, QListView::item:selected:hover {{ background: {d['selection_hover']}; color: {text}; }}
QHeaderView {{ background: {surface}; border: 0; }}
QHeaderView::section {{
    background: {surface};
    color: {muted};
    font-size: 8pt;
    font-weight: 700;
    border: 0;
    padding: 6px 8px;
}}
QHeaderView::section:hover {{ color: {text}; }}
QHeaderView::down-arrow, QHeaderView::up-arrow {{ width: 8px; height: 8px; }}
QTableCornerButton::section {{ background: {surface}; border: 0; }}
QTableWidget {{ background: {surface}; }}

/* ---- scrollbars ---- */
QScrollBar:vertical {{ width: 8px; background: transparent; margin: 0; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {dim}; }}
QScrollBar:horizontal {{ height: 8px; background: transparent; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {border}; border-radius: 4px; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {dim}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; background: none; border: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QScrollArea {{ border: 0; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QAbstractScrollArea::corner {{ background: transparent; }}

/* ---- tabs ---- */
QTabWidget::pane {{ border: 0; background: {surface}; border-radius: 0; top: -1px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent;
    color: {muted};
    padding: 8px 14px;
    border: 0;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{ color: {text}; }}
QTabBar::tab:selected {{ color: {text}; border-bottom: 2px solid {accent}; }}

/* ---- misc ---- */
QToolTip {{ background: {surface2}; color: {text}; border: 1px solid {border}; padding: 4px 6px; border-radius: {r}px; }}
QMenu {{ background: {surface2}; color: {text}; border: 1px solid {border}; border-radius: {r}px; padding: 4px; }}
QMenu::item {{ padding: 6px 20px 6px 12px; border-radius: 0; background: transparent; }}
QMenu::item:selected {{ background: {d['selection']}; }}
QMenu::item:disabled {{ color: {dim}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 6px; }}
QMenuBar {{ background: {surface}; }}
QMenuBar::item:selected {{ background: {surface2}; }}
QMessageBox {{ background: {surface}; }}
QMessageBox QLabel {{ background: transparent; color: {text}; }}
QMessageBox QPushButton {{ min-width: 72px; }}
QStatusBar {{ background: {surface}; color: {muted}; border: 0; font-size: 9pt; }}
QStatusBar::item {{ border: 0; }}
QStatusBar QLabel {{ color: {muted}; }}
QSplitter::handle {{ background: {bg}; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:vertical {{ height: 6px; }}
QGroupBox {{ border: 1px solid {border}; border-radius: 0; margin-top: 12px; padding-top: 8px; background: transparent; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {muted}; }}
QFrame#HLine {{ background: {border}; max-height: 1px; min-height: 1px; border: 0; }}
QFrame#VLine {{ background: {border}; max-width: 1px; min-width: 1px; border: 0; }}
QAbstractItemView#Palette {{ background: {surface2}; border: 1px solid {border}; border-radius: 0; }}

/* chart palette: {chart_comment} */
"""
    return qss.strip() + "\n"


def build_palette(tokens: dict[str, Any] = TOKENS) -> QPalette:
    """Create the dark :class:`QPalette` matching ``tokens``."""
    t = tokens
    pal = QPalette()
    text = QColor(t["text"])
    dim = QColor(t["text_dim"])
    roles = {
        QPalette.ColorRole.Window: QColor(t["bg"]),
        QPalette.ColorRole.WindowText: text,
        QPalette.ColorRole.Base: QColor(t["surface"]),
        QPalette.ColorRole.AlternateBase: QColor(t["surface2"]),
        QPalette.ColorRole.Text: text,
        QPalette.ColorRole.Button: QColor(t["surface2"]),
        QPalette.ColorRole.ButtonText: text,
        QPalette.ColorRole.Highlight: QColor(t["accent"]),
        QPalette.ColorRole.HighlightedText: text,
        QPalette.ColorRole.ToolTipBase: QColor(t["surface2"]),
        QPalette.ColorRole.ToolTipText: text,
        QPalette.ColorRole.PlaceholderText: dim,
        QPalette.ColorRole.BrightText: QColor(t["danger"]),
        QPalette.ColorRole.Link: QColor(t["accent"]),
        QPalette.ColorRole.LinkVisited: QColor(t["accent2"]),
        QPalette.ColorRole.Light: QColor(t["surface2"]),
        QPalette.ColorRole.Midlight: QColor(t["border"]),
        QPalette.ColorRole.Mid: QColor(t["border"]),
        QPalette.ColorRole.Dark: QColor(t["bg"]),
        QPalette.ColorRole.Shadow: QColor(t["bg"]),
    }
    for role, color in roles.items():
        pal.setColor(role, color)
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.HighlightedText,
    ):
        pal.setColor(QPalette.ColorGroup.Disabled, role, dim)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Highlight, QColor(t["surface2"]))
    return pal


def apply_theme(app: QApplication, tokens: dict[str, Any] = TOKENS) -> None:
    """Install Fusion style, the dark palette, the default font and the stylesheet on ``app``."""
    app.setStyle("Fusion")
    app.setPalette(build_palette(tokens))
    font = QFont(str(tokens["font"]), int(tokens["font_size"]))
    app.setFont(font)
    try:
        ensure_theme_images(tokens)
    except Exception:  # noqa: BLE001 - glyphs are cosmetic; never block start-up
        logger.exception("cannot render theme images")
    app.setStyleSheet(build_qss(tokens))
    # Setting a stylesheet wraps the base style in an (unnamed) QStyleSheetStyle proxy;
    # keep the "fusion" name on the active style so callers/tests can still identify it.
    style = app.style()
    if style is not None and not style.objectName():
        style.setObjectName("fusion")
    logger.debug("theme applied: style=%s", app.style().objectName())
