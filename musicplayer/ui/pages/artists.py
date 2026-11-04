"""Artists overview: every artist with its tracks, albums, years, plays, genres, moods, voice and how much is labelled."""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget,
                               QListWidgetItem, QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from musicplayer.ui.context import AppContext

COLS = ["Исполнитель", "Треков", "Альбомов", "Годы", "Прослушиваний", "Жанры", "Настроения", "Голос", "Язык", "Размечено"]


class NumItem(QTableWidgetItem):
    """Sorts by the number stored in UserRole, not by the text."""

    def __lt__(self, other) -> bool:
        a, b = self.data(Qt.ItemDataRole.UserRole), other.data(Qt.ItemDataRole.UserRole)
        if a is None or b is None:
            return super().__lt__(other)
        return a < b


class _Arrived(QObject):
    photo = Signal(str)              # an artist's photo was fetched (from the fetcher's thread)


class ArtistPictures:
    """Artist pictures like album covers: the photo (core/artistimg.py, fetched in the background), meanwhile the
    cover of the artist's biggest album, else the placeholder.  Square, centre-cropped."""

    def __init__(self, covers) -> None:
        from musicplayer.core import artistimg
        self.covers = covers                       # the album CoverCache: its folder and placeholder
        self.arrived = _Arrived()
        self.fetcher = artistimg.Fetcher(covers.folder, lambda name, _f: self.arrived.photo.emit(name))
        self._cache: dict[tuple[str, int], QPixmap] = {}

    def pixmap(self, name: str, album_cover: str | None, size: int) -> QPixmap:
        from musicplayer.core import artistimg
        f = artistimg.cached(self.covers.folder, name)
        if f is None:
            self.fetcher.want(name)
            return self.covers.pixmap(album_cover, size)
        k = (name, size)
        if len(self._cache) > 3000:
            self._cache.clear()
        if k not in self._cache:
            pm = QPixmap(str(f))
            if pm.isNull():
                return self.covers.pixmap(album_cover, size)
            pm = pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                           Qt.TransformationMode.SmoothTransformation)
            self._cache[k] = pm.copy((pm.width() - size) // 2, (pm.height() - size) // 2, size, size)
        return self._cache[k]


class ArtistsPage(QWidget):
    key = "artists"

    def __init__(self, ctx: AppContext, covers=None, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.rows: list[dict] = []
        self.pictures = ArtistPictures(covers) if covers is not None else None
        self.album_covers: dict[str, str] = {}
        self.tile = max(64, min(400, int(ctx.settings.tile_size)))
        self.zoom = 1.0
        self.search = QLineEdit(placeholderText="Фильтр по исполнителю, жанру, настроению…")
        self.info = QLabel()
        self.btn_show = QPushButton("Показать треки")
        self.btn_play = QPushButton("▶ Играть")
        self.btn_fav = QPushButton("♡ В избранное")
        self.btn_edit = QPushButton("✎ Переименовать…")
        self.btn_edit.setToolTip("Исправить имя исполнителя у всех его треков (только в каталоге)")
        self.btn_radio = QPushButton("Радио")
        self.btn_radio.setToolTip("Волна из треков исполнителя и похожих на них")
        self.btn_link = QPushButton("Поделиться")
        self.btn_link.setToolTip("Скопировать ссылку на исполнителя: откроется у тех, кто слушает эту библиотеку")
        self.btn_dupes = QPushButton("Дубликаты…")
        self.btn_dupes.setToolTip("Один исполнитель под разными написаниями: объединить в одно имя")
        self.btn_view = QPushButton()
        self.btn_view.setCheckable(True)
        self.btn_view.setChecked(bool(ctx.settings.artists_tiles) and self.pictures is not None)
        self.btn_view.setVisible(self.pictures is not None)
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.btn_view)
        for b in (self.btn_show, self.btn_play, self.btn_radio, self.btn_fav, self.btn_link, self.btn_edit, self.btn_dupes):
            top.addWidget(b)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        for i, w in enumerate([220, 60, 70, 90, 100, 220, 240, 70, 60]):
            self.table.setColumnWidth(i, w)
        self.table.setToolTip("Двойной клик — треки исполнителя в библиотеке. Клик по заголовку — сортировка.")
        self.grid = QListWidget()                      # «Плитка»: photos like the album covers
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setWordWrap(True)
        self.grid.setToolTip("Двойной клик — треки исполнителя в библиотеке")
        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.grid)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.info)
        lay.addWidget(self.stack, 1)
        self._layout_tiles()
        self._view_text()
        self.btn_view.toggled.connect(self.set_tiles)
        self.grid.itemDoubleClicked.connect(lambda _it: self.show_tracks())
        self.grid.currentItemChanged.connect(lambda *_: self._fav_text())
        if self.pictures is not None:
            self.pictures.arrived.photo.connect(self._photo_arrived)
        self._timer = QTimer(self, singleShot=True, interval=200)
        self._timer.timeout.connect(self.fill)
        self.search.textChanged.connect(lambda _t: self._timer.start())
        self.table.cellDoubleClicked.connect(lambda r, c: self.show_tracks())
        self.btn_show.clicked.connect(self.show_tracks)
        self.btn_play.clicked.connect(self.play)
        self.btn_edit.clicked.connect(self.rename)
        self.btn_radio.clicked.connect(self.radio)
        self.btn_dupes.clicked.connect(self.dupes)
        self.btn_link.clicked.connect(self.share)
        self.btn_fav.clicked.connect(self.toggle_favorite)
        self.table.currentCellChanged.connect(lambda *_: self._fav_text())
        self._dirty = True
        ctx.events.library_changed.connect(self._mark)
        ctx.events.tracks_removed.connect(lambda ids: self._mark())
        ctx.events.hidden_changed.connect(self._mark)
        ctx.events.tracks_changed.connect(lambda ids: self._mark())

    def _mark(self) -> None:
        self._dirty = True
        if self.isVisible():
            self.refresh()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if self._dirty:
            self.refresh()

    def refresh(self) -> None:
        self._dirty = False
        from musicplayer.core import hidden
        gone = hidden.keys(self.ctx.db, self.ctx.listener)["artist"]
        self.rows = [r for r in self.ctx.db.artists_overview() if (r["artist"] or "").casefold() not in gone]
        self.album_covers = self.ctx.db.artist_covers() if self.pictures is not None else {}
        self.fill()

    def fill(self) -> None:
        q = self.search.text().strip().casefold()
        rows = [r for r in self.rows if not q or q in " ".join([r["artist"], *r["genres"], *r["moods"]]).casefold()]
        tiles = self.btn_view.isChecked()
        self.stack.setCurrentWidget(self.grid if tiles else self.table)
        if tiles:
            self._fill_grid(rows)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            years = f"{r['y0']}–{r['y1']}" if r["y0"] and r["y1"] and r["y0"] != r["y1"] else str(r["y0"] or "—")
            voice = {"male": "муж.", "female": "жен."}
            v = "/".join(voice[x] for x in r["voice"][:2]) or ("инстр." if r["inst"] and r["inst"] >= r["tracks"] / 2 else "—")
            lang = "рус" if (r["ru"] or 0) >= r["tracks"] / 2 else "ин."
            pct = round(100 * (r["labelled"] or 0) / r["tracks"])
            cells = [(r["artist"], None), (str(r["tracks"]), r["tracks"]), (str(r["albums"]), r["albums"]),
                     (years, r["y0"] or 0), (str(r["plays"] or 0), r["plays"] or 0), (", ".join(r["genres"][:3]), None),
                     (", ".join(r["moods"][:3]), None), (v, None), (lang, None), (f"{pct}%", pct)]
            for j, (text, num) in enumerate(cells):
                it = NumItem(text) if num is not None else QTableWidgetItem(text)
                if num is not None:
                    it.setData(Qt.ItemDataRole.UserRole, num)
                    it.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
                if j == 0:
                    it.setData(Qt.ItemDataRole.UserRole + 1, r["artist"])
                    if self.pictures is not None and not tiles:
                        it.setIcon(QIcon(self.pictures.pixmap(r["artist"], self.album_covers.get(r["artist"]), 28)))
                    if r.get("last"):
                        it.setToolTip("Последний раз слушали " + time.strftime("%d.%m.%Y", time.localtime(r["last"])))
                if j == len(cells) - 1:
                    it.setForeground(QColor("#7BD88F" if pct >= 50 else "#E8C46B" if pct >= 10 else "#E88A8A"))
                self.table.setItem(i, j, it)
        self.table.setSortingEnabled(True)
        self.info.setText(f"Исполнителей: {len(rows)}" + (f" из {len(self.rows)}" if q else "") +
                          " · «Размечено» — доля треков с вашими метками настроения")

    def _fill_grid(self, rows: list[dict]) -> None:
        cur = self.current_artist()
        self.grid.clear()
        t = int(self.tile * self.zoom)
        for r in rows:
            it = QListWidgetItem(QIcon(self.pictures.pixmap(r["artist"], self.album_covers.get(r["artist"]), t)),
                                 f"{r['artist']}\n{r['tracks']} тр. · {r['plays'] or 0} просл.")
            it.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            it.setData(Qt.ItemDataRole.UserRole, r["artist"])
            self.grid.addItem(it)
            if r["artist"] == cur:
                self.grid.setCurrentItem(it)

    def _photo_arrived(self, name: str) -> None:
        """A photo came: put it on that artist's tile / row in place of the album cover."""
        if not self.isVisible():
            return
        if self.stack.currentWidget() is self.grid:
            t = int(self.tile * self.zoom)
            for i in range(self.grid.count()):
                it = self.grid.item(i)
                if it.data(Qt.ItemDataRole.UserRole) == name:
                    it.setIcon(QIcon(self.pictures.pixmap(name, None, t)))
                    return
        else:
            for i in range(self.table.rowCount()):
                it = self.table.item(i, 0)
                if it is not None and it.data(Qt.ItemDataRole.UserRole + 1) == name:
                    it.setIcon(QIcon(self.pictures.pixmap(name, None, 28)))
                    return

    def _layout_tiles(self) -> None:
        t = int(self.tile * self.zoom)
        self.grid.setIconSize(QSize(t, t))
        self.grid.setGridSize(QSize(t + 30, t + int(60 * self.zoom)))
        self.table.setIconSize(QSize(28, 28))

    def _view_text(self) -> None:
        tiles = self.btn_view.isChecked()
        self.btn_view.setText("☰ Таблица" if tiles else "▦ Плитка")
        self.btn_view.setToolTip("Показать списком с колонками" if tiles else "Показать плитками с фото исполнителей")

    def set_tiles(self, on: bool) -> None:
        self.ctx.settings.artists_tiles = bool(on)
        self._view_text()
        self.fill()

    def apply_zoom(self, f: float) -> None:
        self.zoom = f
        self._layout_tiles()
        if self.btn_view.isChecked():
            self.fill()

    def current_artist(self) -> str | None:
        if self.stack.currentWidget() is self.grid:
            it = self.grid.currentItem()
            return it.data(Qt.ItemDataRole.UserRole) if it else None
        r = self.table.currentRow()
        it = self.table.item(r, 0) if r >= 0 else None
        return it.data(Qt.ItemDataRole.UserRole + 1) if it else None

    def _tracks(self, artist: str) -> list[dict]:
        return [t for t in self.ctx.db.search(artist=artist, limit=100000)
                if (t.get("albumartist") or t.get("artist")) == artist]

    def show_tracks(self) -> None:
        a = self.current_artist()
        if a:
            self.ctx.events.show_rows.emit(self._tracks(a), a)
            self.ctx.events.navigate.emit("library")

    def play(self) -> None:
        a = self.current_artist()
        if a:
            rows = self._tracks(a)
            if rows:
                self.ctx.player.play_tracks(rows)

    def radio(self) -> None:
        a = self.current_artist()
        if a:
            self.ctx.events.start_radio.emit({"artist": a})

    def share(self) -> None:
        from musicplayer.ui.libraryclient import artist_path, copy_share
        a = self.current_artist()
        if a:
            copy_share(self.ctx, self.btn_link, artist_path(a))

    def dupes(self) -> None:
        from musicplayer.ui.artistdupes_dialog import ArtistDupesDialog
        d = ArtistDupesDialog(self.ctx, self)
        if d.exec() and d.merged:
            self.refresh()

    def _fav_text(self) -> None:
        a = self.current_artist()
        on = bool(a) and self.ctx.is_liked("artist", a)
        self.btn_fav.setText("♥ В избранном (убрать)" if on else "♡ В избранное")

    def toggle_favorite(self) -> None:
        a = self.current_artist()
        if a:
            self.ctx.set_liked("artist", a, not self.ctx.is_liked("artist", a))
            self.ctx.events.favorites_changed.emit()
            self._fav_text()

    def rename(self) -> None:
        from musicplayer.ui.edittrack import EditTrackDialog
        a = self.current_artist()
        if a:
            EditTrackDialog(self.ctx, self._tracks(a), self).exec()
