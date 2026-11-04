"""«Качество звука»: what the nightly check found (core/quality.py) - broken, cut, re-encoded and low-quality files;
mark them for deletion, find them in the library, or save the list for downloading them again."""

from __future__ import annotations

import json
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout)

from musicplayer.core import deletemarks, quality


def redownload_json(tracks: list[dict], reason: str) -> str:
    """The list a download pipeline takes: what to look for and why (the same fields as the delete-marks file)."""
    items = [{"kind": "track", "path": t["path"], "artist": t.get("artist"), "albumartist": t.get("albumartist"),
              "album": t.get("album"), "title": t.get("title"), "year": t.get("year"),
              "reason": t.get("note") or reason, "verdict": t.get("verdict")} for t in tracks]
    return json.dumps({"app": "Walle Player", "what": "redownload", "exported": time.time(), "items": items},
                      ensure_ascii=False, indent=1)


def save_redownload(parent, ctx, tracks: list[dict], reason: str) -> str | None:
    root = Path(ctx.settings.library_root or ".")
    name = f"перекачать {time.strftime('%Y-%m-%d')}.json"
    path, _ = QFileDialog.getSaveFileName(parent, "Список на перекачку", str(root.parent / name), "JSON (*.json)")
    if not path:
        return None
    Path(path).write_text(redownload_json(tracks, reason), encoding="utf-8")
    return path


class QualityDialog(QDialog):
    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("Качество звука")
        self.resize(900, 560)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setObjectName("Muted")
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Трек", "Исполнитель — альбом", "Что не так"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for i, w in enumerate((280, 280)):
            self.tree.setColumnWidth(i, w)
        self.btn_mark = QPushButton("Пометить на удаление")
        self.btn_mark.setToolTip("В список «На удаление» (оттуда — в корзину); выгрузка списка — там же")
        self.btn_show = QPushButton("Показать в библиотеке")
        self.btn_ok = QPushButton("Это нормально")
        self.btn_ok.setToolTip("Убрать из списка: запись так и задумана (старая, концертная…)")
        self.btn_export = QPushButton("Выгрузить на перекачку…")
        self.btn_export.setToolTip("JSON с выбранными (или всеми) треками: что скачать заново и почему")
        self.btn_run = QPushButton("Проверить сейчас")
        self.btn_run.setToolTip("Проверить ещё не проверенные треки (обычно это делается ночью само)")
        row = QHBoxLayout()
        for b in (self.btn_mark, self.btn_show, self.btn_ok):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.btn_export)
        row.addWidget(self.btn_run)
        lay = QVBoxLayout(self)
        lay.addWidget(self.info)
        lay.addWidget(self.tree, 1)
        lay.addLayout(row)
        self.btn_mark.clicked.connect(self.mark)
        self.btn_show.clicked.connect(self.show_selected)
        self.btn_ok.clicked.connect(self.accept_as_is)
        self.btn_export.clicked.connect(self.export)
        self.btn_run.clicked.connect(self.run_now)
        self.tree.itemSelectionChanged.connect(self._buttons)
        self.refresh()

    def refresh(self) -> None:
        self.rows = quality.listing(self.ctx.db)
        s = quality.summary(self.ctx.db)
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for t in self.rows:
            g = groups.get(t["verdict"])
            if g is None:
                g = groups[t["verdict"]] = QTreeWidgetItem([quality.VERDICTS[t["verdict"]]])
                g.setFirstColumnSpanned(True)
                self.tree.addTopLevelItem(g)
            it = QTreeWidgetItem([t["title"] or "", f"{t['artist'] or ''} — {t['album'] or 'без альбома'}", t["note"] or ""])
            it.setData(0, Qt.ItemDataRole.UserRole, t["id"])
            it.setToolTip(0, t["path"])
            g.addChild(it)
        for v, g in groups.items():
            g.setText(0, f"{quality.VERDICTS[v]} ({g.childCount()})")
            g.setExpanded(True)
        left = f", ещё не проверено {s['left']}" if s["left"] else ""
        self.info.setText(f"Проверено треков: {s['checked']}{left}. Битые — {s['broken']}, обрезанные — {s['cut']}, "
                          f"пережатые (выдают себя за 320 кбит/с) — {s['fake']}, низкое качество — {s['low']}. "
                          "Проверка идёт ночью сама, пока программа открыта.")
        self._buttons()

    def _selected(self) -> list[dict]:
        ids = {it.data(0, Qt.ItemDataRole.UserRole) for it in self.tree.selectedItems()}
        for it in self.tree.selectedItems():                  # a group header: all its tracks
            if it.data(0, Qt.ItemDataRole.UserRole) is None:
                ids |= {it.child(i).data(0, Qt.ItemDataRole.UserRole) for i in range(it.childCount())}
        return [t for t in self.rows if t["id"] in ids]

    def _buttons(self) -> None:
        on = bool(self._selected())
        for b in (self.btn_mark, self.btn_show, self.btn_ok):
            b.setEnabled(on)
        self.btn_export.setEnabled(bool(self.rows))
        self.btn_run.setEnabled(not self.ctx.jobs.busy)

    def mark(self) -> None:
        for t in self._selected():
            deletemarks.set_mark(self.ctx.db, "owner", "track", str(t["id"]))
        self.ctx.db.conn.commit()
        self.ctx.events.delete_marks_changed.emit()
        self.info.setText(f"Помечено на удаление: {len(self._selected())}. Список — кнопка «На удаление» слева.")

    def show_selected(self) -> None:
        sel = self._selected()
        if sel:
            self.ctx.events.show_rows.emit(sel, "Качество звука")
            self.ctx.events.navigate.emit("library")

    def accept_as_is(self) -> None:
        for t in self._selected():
            self.ctx.db.conn.execute("UPDATE track_quality SET verdict='ok', note=note || ' (оставлено)' "
                                     "WHERE track_id=?", (t["id"],))
        self.ctx.db.conn.commit()
        self.refresh()

    def export(self) -> None:
        tracks = self._selected() or self.rows
        path = save_redownload(self, self.ctx, tracks, "плохое качество")
        if path:
            self.info.setText(f"Сохранено: {path} ({len(tracks)} треков)")

    def run_now(self) -> None:
        w = self.parent()
        if hasattr(w, "start_quality_check"):
            w.start_quality_check()
        self.accept()
