"""Play queue page."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from musicplayer.ui.context import AppContext
from musicplayer.ui.theme import TOKENS
from musicplayer.ui.widgets import fmt_time


class QueuePage(QWidget):
    key = "queue"

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["#", "Название", "Исполнитель", "Альбом", "Длит."])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 320)
        self.table.setColumnWidth(2, 200)
        self.table.setColumnWidth(3, 240)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.info = QLabel()
        self.btn_remove = QPushButton("Убрать из очереди")
        self.btn_clear = QPushButton("Очистить")
        top = QHBoxLayout()
        top.addWidget(self.info, 1)
        top.addWidget(self.btn_remove)
        top.addWidget(self.btn_clear)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.table, 1)
        self.table.cellDoubleClicked.connect(lambda r, c: ctx.player.jump(self.table.item(r, 0).data(256)))
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_clear.clicked.connect(self.clear)
        ctx.player.queue_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        q = self.ctx.player.queue
        order = q.order
        self.table.setRowCount(len(order))
        cur = q.current_index
        for row, idx in enumerate(order):
            t = q.items[idx]
            vals = [row + 1, t["title"], t["artist"], t["album"] or "", fmt_time(t["duration"])]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                it.setData(256, idx)
                if idx == cur:
                    f = QFont(it.font())
                    f.setBold(True)
                    it.setFont(f)
                    it.setForeground(QColor(TOKENS["accent"]))
                self.table.setItem(row, j, it)
        modes = {"off": "выкл", "random": "случайно", "rare": "случайно, чаще редкие"}
        rep = {"off": "выкл", "all": "всё", "one": "трек"}
        self.info.setText(f"В очереди: {len(order)}.  Перемешивание: {modes[q.shuffle]}.  Повтор: {rep[q.repeat]}")

    def remove_selected(self) -> None:
        idxs = sorted({self.table.item(i.row(), 0).data(256) for i in self.table.selectedIndexes()}, reverse=True)
        for i in idxs:
            self.ctx.player.queue.remove(i)
        self.ctx.player.queue_changed.emit()

    def clear(self) -> None:
        self.ctx.player.stop()
        self.ctx.player.queue.clear()
        self.ctx.player.queue_changed.emit()
        self.ctx.player.track_changed.emit(None)
