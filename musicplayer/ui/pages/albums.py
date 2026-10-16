"""Album grid with covers (falls back to a placeholder)."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QSlider,
                               QVBoxLayout, QWidget)

from musicplayer.ui.context import AppContext
from musicplayer.ui.widgets import CoverCache, JumpSlider, refresh_when_shown


class AlbumsPage(QWidget):
    key = "albums"

    def __init__(self, ctx: AppContext, covers: CoverCache, parent=None) -> None:
        super().__init__(parent)
        self.ctx, self.covers = ctx, covers
        self.search = QLineEdit(placeholderText="Фильтр по альбому или исполнителю")
        self.grid = QListWidget()
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.tile = max(64, min(400, int(ctx.settings.tile_size)))
        self.zoom = 1.0
        self.grid.setWordWrap(True)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.slider = JumpSlider(Qt.Orientation.Horizontal, minimum=64, maximum=400, value=self.tile)
        self.slider.setFixedWidth(220)
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(QLabel("Размер плитки:"))
        top.addWidget(self.slider)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.grid, 1)
        self.search.textChanged.connect(self.refresh)
        self.slider.valueChanged.connect(self.set_tile)
        self.grid.viewport().installEventFilter(self)
        self._layout_tiles()
        self.grid.itemDoubleClicked.connect(self.play_item)
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._menu)
        self.grid.setToolTip("Двойной клик — играть альбом, правый клик — в избранное")
        ctx.events.library_changed.connect(self.refresh)
        refresh_when_shown(self, ctx.events.tracks_removed, self.refresh)
        refresh_when_shown(self, ctx.events.hidden_changed, self.refresh)
        self.refresh()

    def eventFilter(self, obj, ev) -> bool:
        """Ctrl+wheel over the grid resizes the tiles (the window-wide zoom ignores it here)."""
        if obj is self.grid.viewport() and ev.type() == QEvent.Type.Wheel and ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.slider.setValue(self.slider.value() + (16 if ev.angleDelta().y() > 0 else -16))
            return True
        return super().eventFilter(obj, ev)

    def _layout_tiles(self) -> None:
        t = int(self.tile * self.zoom)
        self.grid.setIconSize(QSize(t, t))
        self.grid.setGridSize(QSize(t + 30, t + int(60 * self.zoom)))

    def set_tile(self, size: int) -> None:
        """Tile (cover) size in pixels, 64..400; saved in settings."""
        self.tile = max(64, min(400, int(size)))
        self.ctx.settings.tile_size = self.tile
        if self.slider.value() != self.tile:
            self.slider.setValue(self.tile)
        self._layout_tiles()
        self.refresh()

    def apply_zoom(self, f: float) -> None:
        self.zoom = f
        self._layout_tiles()
        self.refresh()

    def refresh(self) -> None:
        q = self.search.text().strip().casefold()
        self.grid.clear()
        from musicplayer.core import hidden
        for a in hidden.visible_values(self.ctx.db, self.ctx.listener, "album", self.ctx.db.list_values("album")):
            if q and q not in f"{a['name']} {a['artist']}".casefold():
                continue
            it = QListWidgetItem(QIcon(self.covers.pixmap(a["cover_key"], int(self.tile * self.zoom))),
                                 f"{a['name']}\n{a['artist']} · {a['year'] or '—'}")
            it.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            it.setData(Qt.ItemDataRole.UserRole, (a["artist"], a["name"]))
            self.grid.addItem(it)

    def tracks_of(self, artist: str, album: str) -> list[dict]:
        return self.ctx.db.search(artist=artist, album=album, order="album", limit=1000)

    def _menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu
        from musicplayer.ui.pages.favorites import album_key
        item = self.grid.itemAt(pos)
        if item is None:
            return
        artist, album = item.data(Qt.ItemDataRole.UserRole)
        key = album_key(artist, album)
        on = self.ctx.is_liked("album", key)
        m = QMenu(self)
        act = m.addAction("♥ Убрать из избранного" if on else "♡ В избранное")
        play = m.addAction("▶ Играть альбом")
        chosen = m.exec(self.grid.viewport().mapToGlobal(pos))
        if chosen is act:
            self.ctx.set_liked("album", key, not on)
            self.ctx.events.favorites_changed.emit()
        elif chosen is play:
            self.play_item(item)

    def play_item(self, item: QListWidgetItem) -> None:
        artist, album = item.data(Qt.ItemDataRole.UserRole)
        self.ctx.player.play_tracks(self.tracks_of(artist, album))
