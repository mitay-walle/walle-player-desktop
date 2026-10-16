"""«Скрытое»: everything the owner chose «Не показывать» for, grouped by kind, with «Вернуть»."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QPushButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from musicplayer.core import hidden


class HiddenList(QWidget):
    """Grouped list (Треки / Альбомы / Исполнители / Жанры / Метки); «Вернуть» (or a double click, or Delete) shows
    the selected things again everywhere.  ``user``: "owner" here; guests manage theirs in the web player."""

    def __init__(self, ctx, user: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.ctx, self._user = ctx, user
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Что", "Чьё / что это", "Скрыто"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setColumnWidth(0, 340)
        self.tree.setColumnWidth(1, 240)
        self.btn_back = QPushButton("Вернуть")
        self.btn_back.setToolTip("Снова показывать выбранное в списках и в «Моей волне» (Delete, двойной клик)")
        self.info = QLabel()
        self.info.setObjectName("Muted")
        self.info.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.btn_back)
        row.addWidget(self.info, 1)
        lay = QVBoxLayout(self)
        lay.addLayout(row)
        lay.addWidget(self.tree, 1)
        self.btn_back.clicked.connect(self.unhide_selected)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self._unhide([it]))
        from PySide6.QtGui import QKeySequence, QShortcut
        QShortcut(QKeySequence("Delete"), self.tree, activated=self.unhide_selected)
        self.refresh()

    @property
    def user(self) -> str:
        return self._user or self.ctx.listener            # the listener chosen in the app

    def refresh(self) -> None:
        rows = hidden.listing(self.ctx.db, self.user)
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for kind, label in hidden.KINDS.items():
            items = [r for r in rows if r["kind"] == kind]
            if not items:
                continue
            g = QTreeWidgetItem([f"{label} ({len(items)})"])
            g.setFlags(g.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            groups[kind] = g
            self.tree.addTopLevelItem(g)
            for r in items:
                it = QTreeWidgetItem([r["title"], r["sub"], time.strftime("%d.%m.%Y %H:%M", time.localtime(r["added"]))])
                it.setData(0, Qt.ItemDataRole.UserRole, (r["kind"], r["key"]))
                g.addChild(it)
            g.setExpanded(True)
        self.info.setText("Скрытое не играет в «Моей волне» и не показывается в списках и поиске. Случайно скрыли — "
                          "выберите и нажмите «Вернуть»." if rows else
                          "Ничего не скрыто. «Не показывать» — в меню трека (правая кнопка) и на метках волны.")
        self.btn_back.setEnabled(bool(rows))

    def unhide_selected(self) -> None:
        self._unhide(self.tree.selectedItems())

    def _unhide(self, items) -> None:
        done = 0
        for it in items:
            data = it.data(0, Qt.ItemDataRole.UserRole)
            if data:
                hidden.set_hidden(self.ctx.db, self.user, data[0], data[1], False)
                done += 1
        if done:
            self.ctx.events.hidden_changed.emit()
            self.refresh()
