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
    """What was found in the library and what not; «Импортировать» (Enter) adds the found tracks to the likes.
    ``remote(items, dry_run) -> {matched, unmatched, added}``: the listener lives on the library server, which matches
    and imports (its track ids are not this copy's)."""

    def __init__(self, ctx, user: dict, path: str, parent=None, remote=None) -> None:
        super().__init__(parent)
        self.ctx, self.user, self.remote = ctx, user, remote
        self.setWindowTitle("Импорт лайков")
        self.setMinimumWidth(560)
        self.items = items = likeimport.parse(likeimport.read_file(path), path)
        if remote is not None:
            res = remote(items, True)
            self.track_ids, self.added, self.unmatched = [None] * res["matched"], [], res["unmatched"]
        else:
            res = likeimport.match(ctx.db, items)
            self.track_ids = [t["id"] for _, t in res["matched"]]
            self.added = [i.get("added") for i, _ in res["matched"]]      # dates from the export keep the order
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
        if self.remote is not None:
            try:
                self.count = self.remote(self.items, False)["added"]
            except Exception as e:  # noqa: BLE001 - shown to the user
                QMessageBox.warning(self, "Импорт лайков", f"Сервер не ответил: {e}")
                return
        else:
            self.count = likeimport.apply(self.ctx.db, self.user, self.track_ids, self.added)
            self.ctx.events.favorites_changed.emit()
        self.accept()


def import_likes(ctx, user: dict | None, parent=None, remote=None) -> int:
    """Pick a file, show what matched, import on «Импортировать»; returns how many likes were added.
    ``remote``: see LikeImportDialog (a listener of the library server)."""
    if not user:
        return 0
    path, _ = QFileDialog.getOpenFileName(parent, f"Импорт лайков — {user['name']}", "", FILTER)
    if not path:
        return 0
    try:
        dlg = LikeImportDialog(ctx, user, path, parent, remote)
    except OSError as e:
        QMessageBox.warning(parent, "Импорт лайков", f"Файл не прочитан: {e}")
        return 0
    except Exception as e:  # noqa: BLE001 - the server did not answer
        QMessageBox.warning(parent, "Импорт лайков", f"Сервер не ответил: {e}")
        return 0
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return dlg.count
    return 0
