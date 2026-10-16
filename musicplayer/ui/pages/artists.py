"""Artists overview: every artist with its tracks, albums, years, plays, genres, moods, voice and how much is labelled."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from musicplayer.ui.context import AppContext

COLS = ["Исполнитель", "Треков", "Альбомов", "Годы", "Прослушиваний", "Жанры", "Настроения", "Голос", "Язык", "Размечено"]


class NumItem(QTableWidgetItem):
    """Sorts by the number stored in UserRole, not by the text."""

    def __lt__(self, other) -> bool:
        a, b = self.data(Qt.ItemDataRole.UserRole), other.data(Qt.ItemDataRole.UserRole)
        if a is None or b is None:
            return super().__lt__(other)
        return a < b


class ArtistsPage(QWidget):
    key = "artists"

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.rows: list[dict] = []
        self.search = QLineEdit(placeholderText="Фильтр по исполнителю, жанру, настроению…")
        self.info = QLabel()
        self.btn_show = QPushButton("Показать треки")
        self.btn_play = QPushButton("▶ Играть")
        self.btn_fav = QPushButton("♡ В избранное")
        self.btn_edit = QPushButton("✎ Переименовать…")
        self.btn_edit.setToolTip("Исправить имя исполнителя у всех его треков (только в каталоге)")
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        for b in (self.btn_show, self.btn_play, self.btn_fav, self.btn_edit):
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
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.info)
        lay.addWidget(self.table, 1)
        self._timer = QTimer(self, singleShot=True, interval=200)
        self._timer.timeout.connect(self.fill)
        self.search.textChanged.connect(lambda _t: self._timer.start())
        self.table.cellDoubleClicked.connect(lambda r, c: self.show_tracks())
        self.btn_show.clicked.connect(self.show_tracks)
        self.btn_play.clicked.connect(self.play)
        self.btn_edit.clicked.connect(self.rename)
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
        self.fill()

    def fill(self) -> None:
        q = self.search.text().strip().casefold()
        rows = [r for r in self.rows if not q or q in " ".join([r["artist"], *r["genres"], *r["moods"]]).casefold()]
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
                    if r.get("last"):
                        it.setToolTip("Последний раз слушали " + time.strftime("%d.%m.%Y", time.localtime(r["last"])))
                if j == len(cells) - 1:
                    it.setForeground(QColor("#7BD88F" if pct >= 50 else "#E8C46B" if pct >= 10 else "#E88A8A"))
                self.table.setItem(i, j, it)
        self.table.setSortingEnabled(True)
        self.info.setText(f"Исполнителей: {len(rows)}" + (f" из {len(self.rows)}" if q else "") +
                          " · «Размечено» — доля треков с вашими метками настроения")

    def current_artist(self) -> str | None:
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
