"""Favourites: tracks, albums and artists the user hearted."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                               QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from musicplayer.ui.context import AppContext
from musicplayer.ui.widgets import fmt_time

SEP = "\x1f"


def album_key(artist: str | None, album: str | None) -> str:
    return f"{artist or ''}{SEP}{album or ''}"


SORT_KEY = Qt.ItemDataRole.UserRole + 1


class SortItem(QTableWidgetItem):
    """A cell that sorts by its real value (seconds, the time it was liked, text without case) - not by the shown text."""

    def __lt__(self, other) -> bool:
        a, b = self.data(SORT_KEY), other.data(SORT_KEY)
        if a is not None and b is not None:
            return a < b
        return super().__lt__(other)


class FavoritesPage(QWidget):
    key = "favorites"

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.tracks: list[dict] = []
        self.tabs = QTabWidget()
        # tracks
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Название", "Исполнитель", "Альбом", "Длит.", "Добавлен"])
        self.table.horizontalHeader().setToolTip("Клик по заголовку — сортировка (по дате добавления, названию …)")
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        for i, w in enumerate((320, 220, 260)):
            self.table.setColumnWidth(i, w)
        self.btn_play = QPushButton("▶ Играть избранное")
        self.btn_play.setObjectName("Primary")
        self.btn_unfav = QPushButton("Убрать из избранного")
        tw = QWidget()
        tv = QVBoxLayout(tw)
        th = QHBoxLayout()
        th.addWidget(self.btn_play)
        th.addWidget(self.btn_unfav)
        th.addStretch(1)
        tv.addLayout(th)
        tv.addWidget(self.table, 1)
        # albums / artists
        self.albums = QListWidget()
        self.artists = QListWidget()
        self.tabs.addTab(tw, "Треки")
        self.tabs.addTab(self.albums, "Альбомы")
        self.tabs.addTab(self.artists, "Исполнители")
        from musicplayer.ui.hiddenlist import HiddenList
        self.hidden = HiddenList(ctx)
        self.tabs.addTab(self.hidden, "Скрытое")
        ctx.events.hidden_changed.connect(self.hidden.refresh)
        self.info = QLabel()
        self.info.setObjectName("Muted")
        lay = QVBoxLayout(self)
        title = QLabel("Избранное")
        title.setStyleSheet("font-size:26px; font-weight:700;")
        lay.addWidget(title)
        lay.addWidget(self.info)
        lay.addWidget(self.tabs, 1)
        self.table.cellDoubleClicked.connect(lambda r, c: ctx.player.play_tracks(self.shown(), r))
        self.btn_play.clicked.connect(lambda: ctx.player.play_tracks(self.shown()) if self.tracks else None)
        self.btn_unfav.clicked.connect(self.unfav_selected)
        self.albums.itemDoubleClicked.connect(self.play_album)
        self.artists.itemDoubleClicked.connect(self.show_artist)
        self.albums.setToolTip("Двойной клик — играть альбом")
        self.artists.setToolTip("Двойной клик — треки исполнителя в библиотеке")
        self._dirty = True
        ctx.events.library_changed.connect(self._mark)
        ctx.events.tracks_removed.connect(lambda ids: self._mark())
        ctx.events.favorites_changed.connect(self._mark)

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
        db = self.ctx.db
        alive = {r[0] for r in db.conn.execute("SELECT id FROM tracks WHERE missing=0")}   # deleted files drop out
        import time as _time
        from musicplayer.core import likes
        when = {k: a for k, a in likes.rows(db, self.ctx.listener, "track")}
        self.tracks = [t for t in (db.get_track(int(k)) for k in when if int(k) in alive) if t]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.tracks))
        for i, t in enumerate(self.tracks):
            added = when.get(str(t["id"])) or 0
            cells = (t["title"], t["artist"], t["album"] or "", fmt_time(t["duration"]),
                     _time.strftime("%Y-%m-%d %H:%M", _time.localtime(added)) if added else "")
            keys = ((t["title"] or "").casefold(), (t["artist"] or "").casefold(), (t["album"] or "").casefold(),
                    float(t["duration"] or 0), float(added))
            for j, v in enumerate(cells):
                it = SortItem(str(v))
                it.setData(SORT_KEY, keys[j])
                if j == 0:
                    it.setData(Qt.ItemDataRole.UserRole, t["id"])
                self.table.setItem(i, j, it)
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        if not getattr(self, "_sorted_once", False):         # at first newest likes on top; a header click resorts
            self._sorted_once = True
            self.table.sortByColumn(4, Qt.SortOrder.DescendingOrder)
        else:                                                 # a refresh keeps the column the user chose
            self.table.sortByColumn(header.sortIndicatorSection(), header.sortIndicatorOrder())
        self.albums.clear()
        for k in self.ctx.liked("album"):
            artist, _, album = k.partition(SEP)
            it = QListWidgetItem(f"{album}  —  {artist}")
            it.setData(Qt.ItemDataRole.UserRole, (artist, album))
            self.albums.addItem(it)
        self.artists.clear()
        for a in self.ctx.liked("artist"):
            it = QListWidgetItem(a)
            it.setData(Qt.ItemDataRole.UserRole, a)
            self.artists.addItem(it)
        self.info.setText(f"Треков: {len(self.tracks)} · альбомов: {self.albums.count()} · исполнителей: {self.artists.count()}")

    def shown(self) -> list[dict]:
        """The tracks in the table's current order (it may be sorted by a column)."""
        by_id = {t["id"]: t for t in self.tracks}
        return [by_id[self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)] for r in range(self.table.rowCount())
                if self.table.item(r, 0) is not None and self.table.item(r, 0).data(Qt.ItemDataRole.UserRole) in by_id]

    def unfav_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        for r in rows:
            self.ctx.set_liked("track", str(self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)), False)
        if rows:
            self.ctx.events.favorites_changed.emit()

    def play_album(self, item: QListWidgetItem) -> None:
        artist, album = item.data(Qt.ItemDataRole.UserRole)
        rows = self.ctx.db.search(artist=artist, album=album, order="album", limit=1000)
        if rows:
            self.ctx.player.play_tracks(rows)

    def show_artist(self, item: QListWidgetItem) -> None:
        a = item.data(Qt.ItemDataRole.UserRole)
        self.ctx.events.show_rows.emit(self.ctx.db.search(artist=a, limit=100000), a)
        self.ctx.events.navigate.emit("library")
