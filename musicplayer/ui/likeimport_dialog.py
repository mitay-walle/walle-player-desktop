"""«Импорт лайков»: a list of tracks from a file becomes one listener's likes (see core/likeimport.py)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
                               QPlainTextEdit, QPushButton, QVBoxLayout)

from musicplayer.core import likeimport
from musicplayer.ui.theme import TOKENS

FILTER = "Списки треков (*.txt *.csv *.tsv *.json *.m3u *.m3u8);;Все файлы (*)"
HELP = ("Подойдёт текст «Исполнитель — Название» по строке, CSV/TSV с колонками исполнителя и названия (экспорт из "
        "Яндекс Музыки, Spotify и сервисов переноса плейлистов), JSON или плейлист M3U.")


class LikeImportDialog(QDialog):
    """What was found in the library and what not; «Импортировать» (Enter) adds the found tracks to the likes."""

    def __init__(self, ctx, user: dict, path: str, parent=None) -> None:
        super().__init__(parent)
        self.ctx, self.user = ctx, user
        self.setWindowTitle("Импорт лайков")
        self.setMinimumWidth(560)
        items = likeimport.parse(likeimport.read_file(path), path)
        res = likeimport.match(ctx.db, items)
        self.track_ids = [t["id"] for _, t in res["matched"]]
        self.added = [i.get("added") for i, _ in res["matched"]]          # dates from the export keep the order
        self.unmatched = [likeimport.describe(i) for i in res["unmatched"]]
        who = f"{user['name']} (вы)" if user.get("owner") else user["name"]
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>{Path(path).name}</b>: треков в файле — {len(items)}, найдено в библиотеке — "
                      f"<b>{len(self.track_ids)}</b>, не найдено — {len(self.unmatched)}.<br>"
                      f"Найденные станут лайками слушателя <b>{who}</b>"
                      + (" (в «Избранном»)" if user.get("owner") else "") + "; уже отмеченные не повторятся.")
        head.setWordWrap(True)
        lay.addWidget(head)
        if not items:
            note = QLabel("В файле не нашлось ни одного трека. " + HELP)
            note.setWordWrap(True)
            note.setStyleSheet(f"color:{TOKENS['warning']};")
            lay.addWidget(note)
        if self.unmatched:
            lay.addWidget(QLabel("Не найдены (нет в библиотеке или названы иначе):"))
            box = QPlainTextEdit("\n".join(self.unmatched))
            box.setReadOnly(True)
            box.setMaximumHeight(220)
            lay.addWidget(box)
        row = QHBoxLayout()
        save = QPushButton("Сохранить ненайденные…")
        save.setVisible(bool(self.unmatched))
        save.clicked.connect(self._save_unmatched)
        row.addWidget(save)
        row.addStretch(1)
        buttons = QDialogButtonBox()
        self.ok = buttons.addButton("Импортировать", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        self.ok.setDefault(True)                     # Enter imports, Escape cancels
        self.ok.setEnabled(bool(self.track_ids))
        buttons.accepted.connect(self._import)
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)
        lay.addLayout(row)
        self.count = 0

    def _save_unmatched(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Ненайденные треки", "не найдены.txt", "Текст (*.txt)")
        if path:
            Path(path).write_text("\n".join(self.unmatched) + "\n", encoding="utf-8")

    def _import(self) -> None:
        self.count = likeimport.apply(self.ctx.db, self.user, self.track_ids, self.added)
        self.ctx.events.favorites_changed.emit()
        self.accept()


def import_likes(ctx, user: dict | None, parent=None) -> int:
    """Pick a file, show what matched, import on «Импортировать»; returns how many likes were added."""
    if not user:
        return 0
    path, _ = QFileDialog.getOpenFileName(parent, f"Импорт лайков — {user['name']}", "", FILTER)
    if not path:
        return 0
    try:
        dlg = LikeImportDialog(ctx, user, path, parent)
    except OSError as e:
        QMessageBox.warning(parent, "Импорт лайков", f"Файл не прочитан: {e}")
        return 0
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return dlg.count
    return 0
