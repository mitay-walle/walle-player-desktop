"""Listening history: every counted play, newest first (kept in the catalogue, survives restarts)."""

from __future__ import annotations

import time

from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from musicplayer.ui.context import AppContext
from musicplayer.ui.widgets import fmt_time

LIMIT = 500


def when(ts: float) -> str:
    d = time.localtime(ts)
    today = time.localtime()
    if d.tm_yday == today.tm_yday and d.tm_year == today.tm_year:
        return time.strftime("сегодня %H:%M", d)
    return time.strftime("%d.%m.%Y %H:%M", d)


class HistoryPage(QWidget):
    key = "history"

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.rows: list[dict] = []
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Когда", "Название", "Исполнитель", "Длит."])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 140)
        self.table.setColumnWidth(1, 320)
        self.table.setColumnWidth(2, 220)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.info = QLabel()
        self.btn_play = QPushButton("▶ Играть историю")
        top = QHBoxLayout()
        top.addWidget(self.info, 1)
        top.addWidget(self.btn_play)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.table, 1)
        self.table.cellDoubleClicked.connect(lambda r, c: self.play_from(r))
        self.btn_play.clicked.connect(lambda: self.play_from(0))
        ctx.player.play_counted.connect(lambda _tid: self.refresh())
        self.refresh()

    def history(self) -> list[dict]:
        out = []
        for r in self.ctx.db.conn.execute(
                "SELECT p.track_id, p.played_at FROM plays p JOIN tracks t ON t.id=p.track_id WHERE t.missing=0 "
                "ORDER BY p.played_at DESC LIMIT ?", (LIMIT,)).fetchall():
            t = self.ctx.db.get_track(r[0])
            if t:
                out.append({**t, "played_at": r[1]})
        return out

    def refresh(self) -> None:
        self.rows = self.history()
        self.table.setRowCount(len(self.rows))
        for i, t in enumerate(self.rows):
            for j, v in enumerate((when(t["played_at"]), t["title"], t["artist"], fmt_time(t["duration"]))):
                self.table.setItem(i, j, QTableWidgetItem(str(v)))
        self.info.setText(f"Прослушано (последние {LIMIT}): {len(self.rows)}" if self.rows else "История пуста: трек засчитывается после половины или 4 минут")

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.refresh()

    def play_from(self, row: int) -> None:
        if 0 <= row < len(self.rows):
            self.ctx.player.play_tracks(self.rows, row)
