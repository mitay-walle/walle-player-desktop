"""«Дубликаты исполнителей»: spellings of one artist side by side, a tick and the name to keep, then merge
(core/artistdupes.py; the catalogue only, files stay as they are)."""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout, QLabel, QScrollArea,
                               QVBoxLayout, QWidget)

from musicplayer.core import artistdupes
from musicplayer.ui.theme import TOKENS


class ArtistDupesDialog(QDialog):
    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Дубликаты исполнителей")
        self.resize(720, 560)
        self.groups = artistdupes.find(ctx.db)
        self.rows: list[tuple[QCheckBox, QComboBox, dict]] = []
        head = QLabel("Один исполнитель под разными написаниями (регистр, ё/е, «The», знаки). Отмеченные группы получат "
                      "выбранное имя во всех треках — только в каталоге, файлы не меняются; лайки переходят на него."
                      if self.groups else "Дубликатов не найдено.")
        head.setWordWrap(True)
        head.setStyleSheet(f"color:{TOKENS['text_muted']}")
        grid = QGridLayout()
        grid.setColumnStretch(2, 1)
        for i, g in enumerate(self.groups):
            tick = QCheckBox()
            tick.setChecked(True)
            pick = QComboBox()
            pick.setEditable(True)                       # a correct spelling none of the tracks has yet
            pick.addItems([x["name"] for x in g["names"]])
            pick.setMinimumWidth(240)
            what = QLabel(" · ".join(f"{x['name']} ({x['tracks']})" for x in g["names"]))
            what.setWordWrap(True)
            grid.addWidget(tick, i, 0)
            grid.addWidget(pick, i, 1)
            grid.addWidget(what, i, 2)
            self.rows.append((tick, pick, g))
        inner = QWidget()
        inner.setLayout(grid)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        buttons = QDialogButtonBox()
        self.btn_merge = buttons.addButton("Объединить отмеченные", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_merge.setEnabled(bool(self.groups))
        buttons.addButton("Закрыть", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.merge)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(head)
        lay.addWidget(scroll, 1)
        lay.addWidget(buttons)
        self.merged = 0

    def merge(self) -> None:
        tracks = 0
        for tick, pick, g in self.rows:
            into = pick.currentText().strip()
            if tick.isChecked() and into:
                tracks += artistdupes.merge(self.ctx.db, [x["name"] for x in g["names"]] + [into], into)
                self.merged += 1
        if tracks:
            self.ctx.events.library_changed.emit()
            self.ctx.events.favorites_changed.emit()
        self.accept()
