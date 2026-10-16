"""«На удаление»: what listeners marked from their phones / the web player; the owner deletes or takes the mark off."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from musicplayer.core import deletemarks


class DeleteMarksDialog(QDialog):
    """Grouped list (Треки / Альбомы) with who marked and when.  «Удалить…» sends the files to the Recycle Bin (asks
    first, like Delete everywhere), «Снять пометку» keeps them; the list can be saved to a file and loaded elsewhere."""

    def __init__(self, ctx, names: dict[str, str], parent=None) -> None:
        super().__init__(parent)
        self.ctx, self.names = ctx, names
        self.setWindowTitle("Помечено на удаление")
        self.resize(820, 520)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Что", "Исполнитель / альбом", "Кто пометил", "Когда"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for i, w in enumerate((300, 260, 120)):
            self.tree.setColumnWidth(i, w)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setObjectName("Muted")
        self.btn_delete = QPushButton("Удалить…")
        self.btn_delete.setToolTip("Файлы выбранного — в корзину Windows (спросит перед удалением)")
        self.btn_keep = QPushButton("Снять пометку")
        self.btn_keep.setToolTip("Оставить в библиотеке: пометка исчезнет и у слушателя")
        self.btn_show = QPushButton("Показать в библиотеке")
        self.btn_export = QPushButton("Выгрузить в файл…")
        self.btn_export.setToolTip("JSON со всеми помеченными треками (путь, исполнитель, название, кто и когда пометил)")
        self.btn_import = QPushButton("Загрузить из файла…")
        self.btn_import.setToolTip("Пометки из выгруженного файла: треки находятся по пути, иначе по исполнителю и названию")
        row = QHBoxLayout()
        for b in (self.btn_delete, self.btn_keep, self.btn_show):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.btn_export)
        row.addWidget(self.btn_import)
        lay = QVBoxLayout(self)
        lay.addWidget(self.info)
        lay.addWidget(self.tree, 1)
        lay.addLayout(row)
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_keep.clicked.connect(self.keep_selected)
        self.btn_show.clicked.connect(self.show_selected)
        self.btn_export.clicked.connect(self.export)
        self.btn_import.clicked.connect(self.load)
        self.tree.itemSelectionChanged.connect(self._buttons)
        self.refresh()

    def refresh(self) -> None:
        self.groups = deletemarks.listing(self.ctx.db, self.names)
        self.tree.clear()
        for kind, label in deletemarks.KINDS.items():
            items = [g for g in self.groups if g["kind"] == kind]
            if not items:
                continue
            head = QTreeWidgetItem([f"{label} ({len(items)})"])
            head.setFlags(head.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.tree.addTopLevelItem(head)
            for g in items:
                it = QTreeWidgetItem([g["title"], g["sub"], ", ".join(g["by"]),
                                      time.strftime("%d.%m.%Y %H:%M", time.localtime(g["added"]))])
                it.setData(0, Qt.ItemDataRole.UserRole, (g["kind"], g["key"]))
                head.addChild(it)
            head.setExpanded(True)
        n = sum(len(g["tracks"]) for g in self.groups)
        self.info.setText(f"Слушатели пометили на удаление: {len(self.groups)} (треков: {n}). Выберите и нажмите "
                          "«Удалить…» или «Снять пометку»." if self.groups else
                          "Ничего не помечено. Пометить трек или альбом можно в меню трека в веб-плеере и в приложении.")
        self.btn_export.setEnabled(bool(self.groups))
        self._buttons()

    def _selected(self) -> list[dict]:
        keys = {it.data(0, Qt.ItemDataRole.UserRole) for it in self.tree.selectedItems()} - {None}
        return [g for g in self.groups if (g["kind"], g["key"]) in keys]

    def _buttons(self) -> None:
        on = bool(self._selected())
        for b in (self.btn_delete, self.btn_keep, self.btn_show):
            b.setEnabled(on)

    def delete_selected(self) -> None:
        from musicplayer.ui.pages.library import confirm_trash
        tracks = [t for g in self._selected() for t in g["tracks"]]
        if tracks and confirm_trash(self, self.ctx, tracks):        # takes the marks along (drop_deleted)
            self.refresh()

    def keep_selected(self) -> None:
        for g in self._selected():
            deletemarks.clear(self.ctx.db, g["kind"], g["key"])
        self.refresh()
        self.ctx.events.delete_marks_changed.emit()

    def show_selected(self) -> None:
        tracks = [t for g in self._selected() for t in g["tracks"]]
        if tracks:
            self.ctx.events.show_rows.emit(tracks, "Помечено на удаление")
            self.ctx.events.navigate.emit("library")

    def export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Выгрузить список на удаление",
                                              f"на удаление {time.strftime('%Y-%m-%d')}.json", "JSON (*.json)")
        if path:
            Path(path).write_text(deletemarks.export(self.ctx.db, self.names), encoding="utf-8")

    def load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Загрузить список на удаление", "", "JSON (*.json);;Все файлы (*)")
        if not path:
            return
        try:
            res = deletemarks.load(self.ctx.db, Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Список на удаление", f"Файл не прочитан: {e}")
            return
        msg = f"Помечено: {res['marked']}."
        if res["unmatched"]:
            msg += f"\nНе найдены в этой библиотеке ({len(res['unmatched'])}):\n" + "\n".join(res["unmatched"][:15])
        QMessageBox.information(self, "Список на удаление", msg)
        self.refresh()
        self.ctx.events.delete_marks_changed.emit()
