"""Shared widgets: cover cache, track table model/view, wrapping layout for chips."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import (QAbstractTableModel, QItemSelectionModel, QModelIndex, QPoint, QRect, QSize, Qt,
                            Signal)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QLayout, QSlider, QTableView

from musicplayer.ui.theme import TOKENS


class CoverCache:
    """Cover thumbnails by cover_key with a placeholder for tracks without one."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self._cache: dict[tuple[str | None, int], QPixmap] = {}

    def pixmap(self, key: str | None, size: int = 64) -> QPixmap:
        k = (key, size)
        if len(self._cache) > 4000:      # tile-size changes create many sizes; keep memory bounded
            self._cache.clear()
        if k not in self._cache:
            pm = QPixmap(str(self.folder / f"{key}.jpg")) if key else QPixmap()
            if pm.isNull():
                pm = self._placeholder(size)
            else:
                pm = pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            self._cache[k] = pm
        return self._cache[k]

    @staticmethod
    def _placeholder(size: int) -> QPixmap:
        pm = QPixmap(size, size)
        pm.fill(QColor(TOKENS["surface2"]))
        p = QPainter(pm)
        p.setPen(QColor(TOKENS["text_dim"]))
        p.setFont(QFont(TOKENS["font"], max(8, size // 3)))
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "♪")
        p.end()
        return pm


def fmt_time(sec: float | None) -> str:
    if not sec or sec < 0:
        return "0:00"
    s = int(sec)
    return f"{s // 60}:{s % 60:02d}"


def fmt_date(ts: float | None) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else "—"


COLUMNS = [
    ("title", "Название"), ("artist", "Исполнитель"), ("album", "Альбом"), ("year", "Год"),
    ("genres", "Жанры"), ("moods", "Настроения"), ("completeness", "Разметка"), ("play_count", "Всего"), ("plays_month", "30 дн"),
    ("plays_year", "Год"), ("last_played", "Последний раз"), ("duration", "Длит."),
]
RIGHT = {"year", "completeness", "play_count", "plays_month", "plays_year", "duration"}


def completeness(t: dict) -> int:
    """How fully a track is labelled, 0..100: the mean of six parts - moods (3 of yours = full, the model's own
    count as a third), genre, language, vocals, era, male/female voice (an instrumental needs neither)."""
    src = t.get("mood_sources", {})
    mine = sum(1 for m in t.get("moods", []) if src.get(m) in ("manual", "seed"))
    moods = min(mine, 3) / 3 if mine else (1 / 3 if t.get("moods") else 0.0)
    inst = t.get("vocal") == "inst"
    flags = set(t.get("flags", []))
    parts = [moods, bool(t.get("genres")), bool(t.get("lang")) or inst, bool(t.get("vocal")),
             bool(t.get("year") or t.get("era")), inst or bool(flags & {"male", "female"})]
    return round(100 * sum(float(p) for p in parts) / len(parts))


def refresh_when_shown(page, signal, refresh) -> None:
    """Connect ``signal`` to ``refresh`` without disturbing a page that is not on screen: a hidden page only notes it
    is stale and refreshes when it is shown again (its open album / playlist / scroll stay as they were meanwhile)."""
    from PySide6.QtCore import QEvent, QObject

    class _Shown(QObject):
        def eventFilter(self, obj, ev):
            if ev.type() == QEvent.Type.Show and getattr(page, "_stale", False):
                page._stale = False
                refresh()
            return False

    page._stale = False
    page._shown_filter = _Shown(page)
    page.installEventFilter(page._shown_filter)

    def changed(*_):
        if page.isVisible():
            refresh()
        else:
            page._stale = True
    signal.connect(changed)


class TrackModel(QAbstractTableModel):
    def __init__(self, covers: CoverCache, parent=None) -> None:
        super().__init__(parent)
        self.covers = covers
        self.rows: list[dict] = []
        self.icon_px = 40
        self.sort_keys: list[tuple[int, bool]] = []      # (column, descending), most important first
        self._icons: dict = {}

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        if self.sort_keys:
            self._apply_sort()
        self.endResetModel()

    def remove_ids(self, ids) -> int | None:
        """Drop these tracks' rows in place (the view keeps its scroll); returns the first removed row or None."""
        gone = set(ids)
        first = None
        for r in range(len(self.rows) - 1, -1, -1):
            if self.rows[r]["id"] in gone:
                self.beginRemoveRows(QModelIndex(), r, r)
                del self.rows[r]
                self.endRemoveRows()
                first = r
        return first

    # ---- sorting by columns (click: by this column; Ctrl+click: add it as the next key) ---------------
    def _sort_value(self, t: dict, key: str):
        if key == "plays_month":
            return t["plays_by_period"]["month"]
        if key == "plays_year":
            return t["plays_by_period"]["year"]
        if key == "completeness":
            return completeness(t)
        if key == "genres":
            return ", ".join(t["genres"]).casefold() or None
        if key == "moods":
            return ", ".join(t["moods"] + [s["mood"] for s in t["mood_suggestions"]]).casefold() or None
        v = t.get(key)
        return v.casefold() if isinstance(v, str) else v

    def sort_by(self, column: int, add: bool = False) -> None:
        """Click: sort by the column (ascending -> descending -> off).  add (Ctrl+click): keep the other keys and
        make this one the next key, or flip / drop it if it is already there."""
        keys = list(self.sort_keys)
        pos = next((i for i, (c, _) in enumerate(keys) if c == column), None)
        if pos is None:
            keys = keys + [(column, False)] if add else [(column, False)]
        elif not keys[pos][1]:
            keys[pos] = (column, True)
            if not add:
                keys = [keys[pos]]
        else:
            keys.pop(pos) if add else keys.clear()
        self.sort_keys = keys
        self.layoutAboutToBeChanged.emit()
        self._apply_sort()
        self.layoutChanged.emit()
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(COLUMNS) - 1)

    def _apply_sort(self) -> None:
        for col, desc in reversed(self.sort_keys):      # stable sorts, least important key first
            key = COLUMNS[col][0]
            filled = [t for t in self.rows if self._sort_value(t, key) not in (None, "")]
            empty = [t for t in self.rows if self._sort_value(t, key) in (None, "")]
            filled.sort(key=lambda t: self._sort_value(t, key), reverse=desc)
            self.rows = filled + empty                    # empty values always at the bottom


    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            pos = next((i for i, (c, _) in enumerate(self.sort_keys) if c == section), None)
            if pos is None:
                return COLUMNS[section][1]
            arrow = "▼" if self.sort_keys[pos][1] else "▲"
            return f"{COLUMNS[section][1]} {arrow}" + (str(pos + 1) if len(self.sort_keys) > 1 else "")
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.ToolTipRole:
            return "Клик — сортировать по столбцу (повторный клик меняет порядок, третий — сбрасывает).\n" \
                   "Ctrl+клик — добавить столбец к сортировке."
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        t = self.rows[index.row()]
        key = COLUMNS[index.column()][0]
        if role == Qt.ItemDataRole.DisplayRole:
            if key == "genres":
                return ", ".join(t["genres"])
            if key == "moods":
                sug = [f"{s['mood']}? {int((s['confidence'] or 0) * 100)}%" for s in t["mood_suggestions"]]
                src = t.get("mood_sources", {})
                labels = [("≈" if src.get(m) == "auto" else "") + m for m in t["moods"]]
                return ", ".join(labels + sug)
            if key == "plays_month":
                return t["plays_by_period"]["month"]
            if key == "plays_year":
                return t["plays_by_period"]["year"]
            if key == "completeness":
                return f"{completeness(t)}%"
            if key == "last_played":
                return fmt_date(t["last_played"])
            if key == "duration":
                return fmt_time(t["duration"])
            if key == "title" and t.get("flags"):
                return f"{t['title']}   [{', '.join(t['flags'])}]"
            if key == "artist" and t.get("feat"):
                return f"{t['artist']} feat. {', '.join(t['feat'])}"
            v = t.get(key)
            return "" if v is None else v
        if role == Qt.ItemDataRole.DecorationRole and key == "title":
            k = (t["cover_key"], self.icon_px)
            icon = self._icons.get(k)
            if icon is None:
                if len(self._icons) > 3000:
                    self._icons.clear()
                icon = self._icons[k] = QIcon(self.covers.pixmap(t["cover_key"], self.icon_px))
            return icon
        if role == Qt.ItemDataRole.ForegroundRole and key == "moods" and not t["moods"] and t["mood_suggestions"]:
            return QColor(TOKENS["warning"])
        if role == Qt.ItemDataRole.ForegroundRole and key == "completeness":
            c = completeness(t)
            return QColor("#7BD88F" if c >= 80 else "#E8C46B" if c >= 50 else "#E88A8A")
        if role == Qt.ItemDataRole.ToolTipRole and key == "completeness":
            return ("Полнота разметки: настроения (3 ваших = полностью), жанр, язык, вокал, эпоха, "
                    "мужской/женский голос")
        if role == Qt.ItemDataRole.TextAlignmentRole and key in RIGHT:
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.UserRole:
            return t["id"]
        return None


class TrackView(QTableView):
    play_requested = Signal(int)          # row
    ids_selected = Signal(list)

    def __init__(self, model: TrackModel, parent=None) -> None:
        super().__init__(parent)
        self.setModel(model)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setIconSize(QSize(36, 36))
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(42)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)   # smooth scrolling of long lists
        self.verticalScrollBar().setSingleStep(24)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.apply_zoom(1.0)
        self.doubleClicked.connect(lambda ix: self.play_requested.emit(ix.row()))
        self.horizontalHeader().setSectionsClickable(True)
        self.horizontalHeader().sectionClicked.connect(self._header_clicked)
        self.selectionModel().selectionChanged.connect(lambda *_: self.ids_selected.emit(self.selected_ids()))

    def _header_clicked(self, column: int) -> None:
        from PySide6.QtWidgets import QApplication
        keep = self.selected_ids()
        ctrl = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)
        self.model().sort_by(column, add=ctrl)
        if keep:
            self.select_ids(keep)

    BASE_WIDTHS = [260, 170, 190, 56, 170, 190, 70, 56, 56, 50, 100, 56]

    def apply_zoom(self, f: float) -> None:
        """Scale rows, cover icons and column widths (UI zoom)."""
        px = max(24, int(40 * f))
        m = self.model()
        m.icon_px = px
        self.setIconSize(QSize(px - 4, px - 4))
        self.verticalHeader().setDefaultSectionSize(int(42 * f))
        for i, w in enumerate(self.BASE_WIDTHS):
            self.setColumnWidth(i, int(w * f))
        m.layoutChanged.emit()

    def selected_ids(self) -> list[int]:
        rows = sorted({i.row() for i in self.selectionModel().selectedRows()})
        return [self.model().rows[r]["id"] for r in rows]

    def select_ids(self, ids: list[int]) -> None:
        want = set(ids)
        sm, m = self.selectionModel(), self.model()
        sm.clearSelection()
        first = None
        for r, t in enumerate(m.rows):
            if t["id"] in want:
                sm.select(m.index(r, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
                first = r if first is None else first
        if first is not None:
            self.scrollTo(m.index(first, 0))


class FlowLayout(QLayout):
    """Wrapping layout for chips."""

    def __init__(self, parent=None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items = []
        self.header = True                # first item is a section header (own row in grid mode)
        self.columns = 0                  # >0: equal-width grid with that many columns (the first item - a header - gets its own row)
        self.setSpacing(spacing)

    def set_columns(self, n: int) -> None:
        if n != self.columns:
            self.columns = n
            self.invalidate()

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return self._layout(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._layout(rect, False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        """Widest item, and the height the items need at the current width (so parents never squeeze the rows)."""
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        w = self.geometry().width()
        if w > 0:
            m = self.contentsMargins()
            s.setHeight(max(s.height(), self.heightForWidth(w) + m.top() + m.bottom()))
        return s

    def _grid(self, rect: QRect, test: bool) -> int:
        n, sp = self.columns, self.spacing()
        cw = max((rect.width() - sp * (n - 1)) // n, 1)
        x, y, row_h, col = rect.x(), rect.y(), 0, 0
        for i, it in enumerate(self._items):
            h = it.sizeHint().height()
            if self.header and i == 0 and len(self._items) > 1:               # header on its own line
                if not test:
                    it.setGeometry(QRect(QPoint(x, y), QSize(rect.width(), h)))
                y += h + sp
                continue
            if not test:
                it.setGeometry(QRect(QPoint(x + col * (cw + sp), y), QSize(cw, h)))
            row_h = max(row_h, h)
            col += 1
            if col == n:
                col, y, row_h = 0, y + row_h + sp, 0
        return (y + row_h if col else y - sp) - rect.y()

    def _layout(self, rect: QRect, test: bool) -> int:
        if self.columns:
            return self._grid(rect, test)
        x, y, row_h = rect.x(), rect.y(), 0
        for it in self._items:
            w = it.sizeHint()
            if x + w.width() > rect.right() and row_h:
                x, y, row_h = rect.x(), y + row_h + self.spacing(), 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), w))
            x += w.width() + self.spacing()
            row_h = max(row_h, w.height())
        return y + row_h - rect.y()


class JumpSlider(QSlider):
    """Slider that jumps to the clicked point (not only the handle drags) and follows the mouse while held."""

    def _value_at(self, e) -> int:
        from PySide6.QtWidgets import QStyle
        if self.orientation() == Qt.Orientation.Horizontal:
            return QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), int(e.position().x()), max(1, self.width()))
        return QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), int(e.position().y()), max(1, self.height()),
                                              upsideDown=True)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(True)
            self.setValue(self._value_at(e))
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self.isSliderDown():
            self.setValue(self._value_at(e))
            e.accept()

    def mouseReleaseEvent(self, e) -> None:
        if self.isSliderDown() and e.button() == Qt.MouseButton.LeftButton:
            self.setValue(self._value_at(e))
            self.setSliderDown(False)
            e.accept()
        else:
            super().mouseReleaseEvent(e)
