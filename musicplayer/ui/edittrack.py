"""Correct title / artist / album / year of tracks (catalogue only; the corrections survive rescans)."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout

from musicplayer.ui.context import AppContext


class EditTrackDialog(QDialog):
    FIELDS = [("title", "Название"), ("artist", "Исполнитель"), ("albumartist", "Исполнитель альбома"),
              ("album", "Альбом"), ("year", "Год")]

    def __init__(self, ctx: AppContext, tracks: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.ctx, self.tracks = ctx, tracks
        many = len(tracks) > 1
        self.setWindowTitle(f"Исправить {len(tracks)} треков" if many else "Исправить трек")
        self.edits: dict[str, QLineEdit] = {}
        form = QFormLayout()
        for key, label in self.FIELDS:
            vals = {str(t.get(key) or "") for t in tracks}
            e = QLineEdit(vals.pop() if len(vals) == 1 else "")
            if len(vals) >= 1:                       # several different values: leave empty = keep each as is
                e.setPlaceholderText("разные — оставьте пустым, чтобы не менять")
            if many and key == "title":
                e.setEnabled(False)
                e.setPlaceholderText("у нескольких треков название не меняется")
            self.edits[key] = e
            form.addRow(label, e)
        note = QLabel("Меняется только каталог плеера, файлы не трогаются. Исправления сохраняются при пересканировании.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#8A9099; font-size:11px;")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.button(QDialogButtonBox.StandardButton.Save)
        ok.setText("Сохранить  (Enter)")
        ok.setDefault(True)                          # Enter in any field saves
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Отмена  (Esc)")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(note)
        lay.addWidget(buttons)
        self.resize(520, self.sizeHint().height())

    def changes(self) -> dict:
        out = {}
        for key, e in self.edits.items():
            if not e.isEnabled():
                continue
            v = e.text().strip()
            vals = {str(t.get(key) or "") for t in self.tracks}
            if len(vals) > 1 and not v:
                continue                             # mixed values left empty: keep each track's own
            if not (len(vals) == 1 and v == next(iter(vals))):
                out[key] = v
        return out

    def save(self) -> None:
        ch = self.changes()
        if ch:
            ids = [t["id"] for t in self.tracks]
            self.ctx.db.edit_track(ids, ch)
            self.ctx.events.tracks_changed.emit(ids)
            self.ctx.events.library_changed.emit()
        self.accept()
