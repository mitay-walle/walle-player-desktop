"""Listening statistics: what you play most, what you have not heard for a long time."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from musicplayer.ui.context import AppContext
from musicplayer.ui.theme import TOKENS
from musicplayer.ui.widgets import fmt_date

PERIODS = [("за всё время", None), ("за неделю", 7), ("за месяц", 30), ("за 3 месяца", 91),
           ("за полгода", 182), ("за год", 365)]


def table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setStretchLastSection(True)
    return t


def fill(t: QTableWidget, rows: list[list]) -> None:
    t.setRowCount(len(rows))
    for i, r in enumerate(rows):
        for j, v in enumerate(r):
            it = QTableWidgetItem(str(v))
            if isinstance(v, int):
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            t.setItem(i, j, it)


class StatsPage(QWidget):
    key = "stats"

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.period = QComboBox()
        for label, _ in PERIODS:
            self.period.addItem(label)
        self.period.setCurrentIndex(2)
        self.summary = QLabel()
        self.summary.setStyleSheet(f"color:{TOKENS['text_muted']}")
        self.top_tracks = table(["Трек", "Исполнитель", "Прослушиваний"])
        self.top_artists = table(["Исполнитель", "Прослушиваний"])
        self.top_genres = table(["Жанр", "Прослушиваний"])
        self.stale = table(["Давно не слушал", "Исполнитель", "Последний раз", "Всего"])
        self.never = QLabel()
        top = QHBoxLayout()
        top.addWidget(QLabel("<b>Что слушаю чаще</b>"))
        top.addWidget(self.period)
        top.addWidget(self.summary, 1)
        row = QHBoxLayout()
        for title, w in (("Треки", self.top_tracks), ("Исполнители", self.top_artists), ("Жанры", self.top_genres)):
            v = QVBoxLayout()
            v.addWidget(QLabel(title))
            v.addWidget(w)
            row.addLayout(v)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(row, 1)
        lay.addWidget(QLabel("<b>Что давно не слушал</b>"))
        lay.addWidget(self.stale, 1)
        self.period.currentIndexChanged.connect(self.refresh)
        ctx.events.tracks_changed.connect(lambda ids: self.refresh() if self.isVisible() else setattr(self, "_dirty", True))
        ctx.events.library_changed.connect(self.refresh)
        ctx.events.tracks_removed.connect(lambda ids: self.refresh() if self.isVisible() else setattr(self, "_dirty", True))
        self.top_tracks.cellDoubleClicked.connect(lambda r, c: self._play(self.top_tracks, r))
        self.stale.cellDoubleClicked.connect(lambda r, c: self._play(self.stale, r))
        self.refresh()

    def _play(self, t: QTableWidget, row: int) -> None:
        tid = t.item(row, 0).data(Qt.ItemDataRole.UserRole)
        tr = self.ctx.db.get_track(tid)
        if tr:
            self.ctx.player.play_tracks([tr])

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if getattr(self, "_dirty", False):
            self._dirty = False
            self.refresh()

    def refresh(self) -> None:
        days = PERIODS[self.period.currentIndex()][1]
        s = self.ctx.db.stats(top=25, period_days=days)
        p = s["plays_total_by_period"]
        self.summary.setText(f"Треков: {s['total']}, ни разу не слушал: {s['never_played']}.  Прослушиваний: "
                             f"неделя {p['week']}, месяц {p['month']}, год {p['year']}")
        fill(self.top_tracks, [[t["title"], t["artist"], t["plays"]] for t in s["most_played"]])
        for i, t in enumerate(s["most_played"]):
            self.top_tracks.item(i, 0).setData(Qt.ItemDataRole.UserRole, t["id"])
        fill(self.top_artists, [[a["artist"], a["plays"]] for a in s["top_artists"]])
        fill(self.top_genres, [[g["genre"], g["plays"]] for g in s["top_genres"]])
        fill(self.stale, [[t["title"], t["artist"], fmt_date(t["last_played"]), t["play_count"]]
                          for t in s["not_played_recently"]])
        for i, t in enumerate(s["not_played_recently"]):
            self.stale.item(i, 0).setData(Qt.ItemDataRole.UserRole, t["id"])
