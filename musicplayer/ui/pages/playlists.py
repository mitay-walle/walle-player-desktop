"""Playlists and smart playlists (rules: genre + mood + plays in a period + not played for N days ...)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QListWidget, QMenu, QMessageBox, QPushButton, QSpinBox, QSplitter,
                               QVBoxLayout, QWidget)

from musicplayer.ui.context import AppContext
from musicplayer.ui.widgets import CoverCache, TrackModel, TrackView

ORDERS = [("Исполнитель", "artist"), ("Случайно", "random"), ("Случайно, чаще редкие", "rare_random"),
          ("Меньше прослушиваний", "play_count"), ("Давно не слушал", "last_played"), ("Год", "year")]
PERIODS = [("за всё время", None), ("за неделю", 7), ("за месяц", 30), ("за год", 365)]


class SmartDialog(QDialog):
    def __init__(self, ctx: AppContext, rules: dict | None = None, name: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Умный плейлист")
        r = rules or {}
        self.name = QLineEdit(name)
        self.genre = QComboBox(editable=True)
        self.genre.addItems([""] + [g["name"] for g in ctx.db.list_values("genre")])
        self.genre.setCurrentText(r.get("genre") or "")
        self.mood = QComboBox()
        self.mood.addItems([""] + [m["name"] for m in ctx.db.list_moods()])
        self.mood.setCurrentText(r.get("mood") or "")
        self.text = QLineEdit(r.get("text") or "")
        self.y1 = QSpinBox(minimum=0, maximum=2100, value=r.get("year_from") or 0)
        self.y2 = QSpinBox(minimum=0, maximum=2100, value=r.get("year_to") or 0)
        self.max_on = QCheckBox("прослушиваний не больше")
        self.max_on.setChecked(r.get("max_plays") is not None)
        self.max_plays = QSpinBox(maximum=9999, value=r.get("max_plays") or 0)
        self.min_on = QCheckBox("прослушиваний не меньше")
        self.min_on.setChecked(r.get("min_plays") is not None)
        self.min_plays = QSpinBox(maximum=9999, value=r.get("min_plays") or 1)
        self.period = QComboBox()
        for label, d in PERIODS:
            self.period.addItem(label)
        self.period.setCurrentIndex(max(0, [d for _, d in PERIODS].index(r.get("plays_days"))
                                        if r.get("plays_days") in [d for _, d in PERIODS] else 0))
        self.stale_on = QCheckBox("не слушал дней ≥")
        self.stale_on.setChecked(r.get("not_played_days") is not None)
        self.stale = QSpinBox(maximum=9999, value=r.get("not_played_days") or 30)
        self.order = QComboBox()
        for label, _ in ORDERS:
            self.order.addItem(label)
        keys = [k for _, k in ORDERS]
        self.order.setCurrentIndex(keys.index(r["order"]) if r.get("order") in keys else 0)
        self.limit = QSpinBox(minimum=1, maximum=5000, value=r.get("limit") or 100)
        f = QFormLayout()
        f.addRow("Название", self.name)
        f.addRow("Жанр", self.genre)
        f.addRow("Настроение", self.mood)
        f.addRow("Текст", self.text)
        f.addRow("Год от", self.y1)
        f.addRow("Год до", self.y2)
        f.addRow(self.max_on, self.max_plays)
        f.addRow(self.min_on, self.min_plays)
        f.addRow("Период для прослушиваний", self.period)
        f.addRow(self.stale_on, self.stale)
        f.addRow("Порядок", self.order)
        f.addRow("Не больше треков", self.limit)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v = QVBoxLayout(self)
        v.addLayout(f)
        v.addWidget(bb)

    def rules(self) -> dict:
        r: dict = {"order": ORDERS[self.order.currentIndex()][1], "limit": self.limit.value()}
        if self.genre.currentText().strip():
            r["genre"] = self.genre.currentText().strip()
        if self.mood.currentText():
            r["mood"] = self.mood.currentText()
        if self.text.text().strip():
            r["text"] = self.text.text().strip()
        if self.y1.value():
            r["year_from"] = self.y1.value()
        if self.y2.value():
            r["year_to"] = self.y2.value()
        if self.max_on.isChecked():
            r["max_plays"] = self.max_plays.value()
        if self.min_on.isChecked():
            r["min_plays"] = self.min_plays.value()
        days = PERIODS[self.period.currentIndex()][1]
        if days and (self.max_on.isChecked() or self.min_on.isChecked()):
            r["plays_days"] = days
        if self.stale_on.isChecked():
            r["not_played_days"] = self.stale.value()
        return r


def playlist_menu(parent: QWidget, ctx: AppContext, ids: list[int]) -> None:
    """Popup 'add these tracks to a playlist' (plain playlists only), with 'New…'."""
    menu = QMenu(parent)
    plain = [p for p in ctx.db.list_playlists() if not p["rules"]]
    for p in plain:
        menu.addAction(p["name"], lambda p=p: (ctx.db.playlist_add(p["id"], ids), ctx.events.library_changed.emit()))

    def new() -> None:
        name, ok = QInputDialog.getText(parent, "Новый плейлист", "Название:")
        if ok and name.strip():
            pid = ctx.db.create_playlist(name.strip())
            ctx.db.playlist_add(pid, ids)
            ctx.events.library_changed.emit()
    menu.addSeparator()
    menu.addAction("Новый плейлист…", new)
    from PySide6.QtGui import QCursor
    menu.exec(QCursor.pos())


class PlaylistsPage(QWidget):
    key = "playlists"

    def __init__(self, ctx: AppContext, covers: CoverCache, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.lst = QListWidget()
        self.btn_new = QPushButton("＋ Плейлист")
        self.btn_smart = QPushButton("＋ Умный")
        self.btn_edit = QPushButton("Правила…")
        self.btn_del = QPushButton("Удалить")
        self.btn_play = QPushButton("▶ Играть")
        self.title = QLabel()
        self.model = TrackModel(covers)
        self.view = TrackView(self.model)
        self.btn_remove = QPushButton("Убрать из плейлиста")
        lv = QVBoxLayout()
        lv.addWidget(self.lst, 1)
        for row in ((self.btn_new, self.btn_smart), (self.btn_edit, self.btn_del)):
            h = QHBoxLayout()
            for b in row:
                h.addWidget(b)
            lv.addLayout(h)
        left = QWidget()
        left.setLayout(lv)
        rv = QVBoxLayout()
        h = QHBoxLayout()
        h.addWidget(self.title, 1)
        h.addWidget(self.btn_remove)
        h.addWidget(self.btn_play)
        rv.addLayout(h)
        rv.addWidget(self.view, 1)
        right = QWidget()
        right.setLayout(rv)
        sp = QSplitter()
        sp.addWidget(left)
        sp.addWidget(right)
        sp.setSizes([280, 900])
        lay = QVBoxLayout(self)
        lay.addWidget(sp)
        self.btn_new.clicked.connect(lambda: self.create())
        self.btn_smart.clicked.connect(lambda: self.create_smart())
        self.btn_edit.clicked.connect(self.edit_rules)
        self.btn_del.clicked.connect(self.delete)
        self.btn_play.clicked.connect(self.play)
        self.btn_remove.clicked.connect(self.remove_selected)
        self.lst.currentRowChanged.connect(lambda *_: self.show_current())
        self.view.play_requested.connect(lambda row: ctx.player.play_tracks(self.model.rows, row))
        ctx.events.library_changed.connect(self.refresh)
        ctx.events.tracks_changed.connect(lambda ids: self.show_current())
        self.refresh()

    def apply_zoom(self, f: float) -> None:
        self.view.apply_zoom(f)

    def refresh(self) -> None:
        keep = self.current_id()
        self.lst.blockSignals(True)
        self.lst.clear()
        for p in self.ctx.db.list_playlists():
            self.lst.addItem(f"{'★ ' if p['rules'] else ''}{p['name']}   ({p['count']})")
            self.lst.item(self.lst.count() - 1).setData(Qt.ItemDataRole.UserRole, p["id"])
            if p["id"] == keep:
                self.lst.setCurrentRow(self.lst.count() - 1)
        self.lst.blockSignals(False)
        self.show_current()

    def current_id(self) -> int | None:
        it = self.lst.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def show_current(self) -> None:
        pid = self.current_id()
        p = self.ctx.db.playlist_row(pid) if pid else None
        for b in (self.btn_del, self.btn_play):
            b.setEnabled(p is not None)
        self.btn_edit.setEnabled(bool(p and p["rules"]))
        self.btn_remove.setEnabled(bool(p and not p["rules"]))
        if not p:
            self.title.setText("Выберите плейлист")
            self.model.set_rows([])
            return
        rows = self.ctx.db.playlist_tracks(pid)
        self.title.setText(f"<b>{p['name']}</b> — {'умный, ' if p['rules'] else ''}{len(rows)} треков")
        self.model.set_rows(rows)

    # ---- actions ------------------------------------------------------
    def create(self, name: str | None = None) -> int | None:
        if not name:
            name, ok = QInputDialog.getText(self, "Новый плейлист", "Название:")
            if not ok:
                return None
        if not name.strip():
            return None
        pid = self.ctx.db.create_playlist(name.strip())
        self.ctx.events.library_changed.emit()
        return pid

    def create_smart(self) -> int | None:
        dlg = SmartDialog(self.ctx, parent=self)
        if dlg.exec() and dlg.name.text().strip():
            pid = self.ctx.db.create_playlist(dlg.name.text().strip(), dlg.rules())
            self.ctx.events.library_changed.emit()
            return pid
        return None

    def edit_rules(self) -> None:
        pid = self.current_id()
        p = self.ctx.db.playlist_row(pid) if pid else None
        if p and p["rules"]:
            dlg = SmartDialog(self.ctx, p["rules"], p["name"], self)
            if dlg.exec():
                self.ctx.db.update_playlist(pid, dlg.name.text().strip() or p["name"], dlg.rules())
                self.ctx.events.library_changed.emit()

    def delete(self) -> None:
        pid = self.current_id()
        if pid and QMessageBox.question(self, "Удалить плейлист", "Удалить плейлист? Треки останутся в библиотеке."
                                        ) == QMessageBox.StandardButton.Yes:
            self.ctx.db.delete_playlist(pid)
            self.ctx.events.library_changed.emit()

    def remove_selected(self) -> None:
        pid = self.current_id()
        if pid:
            self.ctx.db.playlist_remove(pid, self.view.selected_ids())
            self.ctx.events.library_changed.emit()

    def play(self) -> None:
        if self.model.rows:
            self.ctx.player.play_tracks(self.model.rows)

    def select_playlist(self, pid: int) -> None:
        for i in range(self.lst.count()):
            if self.lst.item(i).data(Qt.ItemDataRole.UserRole) == pid:
                self.lst.setCurrentRow(i)
