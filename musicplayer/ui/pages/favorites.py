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


class FavoritesPage(QWidget):
    key = "favorites"

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.tracks: list[dict] = []
        self.tabs = QTabWidget()
        # tracks
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Название", "Исполнитель", "Альбом", "Длит."])
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
        self.info = QLabel()
        self.info.setObjectName("Muted")
        lay = QVBoxLayout(self)
        title = QLabel("Избранное")
        title.setStyleSheet("font-size:26px; font-weight:700;")
        lay.addWidget(title)
        lay.addWidget(self.info)
        lay.addWidget(self.tabs, 1)
        self.table.cellDoubleClicked.connect(lambda r, c: ctx.player.play_tracks(self.tracks, r))
        self.btn_play.clicked.connect(lambda: ctx.player.play_tracks(self.tracks) if self.tracks else None)
        self.btn_unfav.clicked.connect(self.unfav_selected)
        self.albums.itemDoubleClicked.connect(self.play_album)
        self.artists.itemDoubleClicked.connect(self.show_artist)
        self.albums.setToolTip("Двойной клик — играть альбом")
        self.artists.setToolTip("Двойной клик — треки исполнителя в библиотеке")
        self._dirty = True
        ctx.events.library_changed.connect(self._mark)
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
        self.tracks = [t for t in (db.get_track(int(k)) for k in db.favorites("track")) if t]
        self.table.setRowCount(len(self.tracks))
        for i, t in enumerate(self.tracks):
            for j, v in enumerate((t["title"], t["artist"], t["album"] or "", fmt_time(t["duration"]))):
                self.table.setItem(i, j, QTableWidgetItem(str(v)))
        self.albums.clear()
        for k in db.favorites("album"):
            artist, _, album = k.partition(SEP)
            it = QListWidgetItem(f"{album}  —  {artist}")
            it.setData(Qt.ItemDataRole.UserRole, (artist, album))
            self.albums.addItem(it)
        self.artists.clear()
        for a in db.favorites("artist"):
            it = QListWidgetItem(a)
            it.setData(Qt.ItemDataRole.UserRole, a)
            self.artists.addItem(it)
        self.info.setText(f"Треков: {len(self.tracks)} · альбомов: {self.albums.count()} · исполнителей: {self.artists.count()}")

    def unfav_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        for r in rows:
            self.ctx.db.set_favorite("track", str(self.tracks[r]["id"]), False)
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
