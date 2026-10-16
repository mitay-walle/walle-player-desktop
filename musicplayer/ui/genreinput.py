"""Genre text input: hierarchical paths «Punk/Pop Punk, Metal» with suggestions for the genre being typed."""

from __future__ import annotations

from PySide6.QtCore import QStringListModel, Qt
from PySide6.QtWidgets import QCompleter, QLineEdit

from musicplayer.core.genres import path_suggestions

SEPARATORS = ",;"


class GenreLineEdit(QLineEdit):
    """Comma-separated genre paths; the popup suggests the tree («Rock», «Rock/Alternative» ...) and the library's own
    genres for the item under the cursor (typing «punk» offers «Punk», «Punk/Pop Punk» ...); Enter / a click on a
    suggestion puts it in place of the typed item."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setPlaceholderText("Жанры: Семейство/Поджанр через запятую — Punk/Pop Punk, Metal")
        self.setToolTip("Жанры через запятую, поджанр — через «/» после семейства: Punk/Pop Punk, Metal/Djent, Folk.\n"
                        "Подсказки — по уже известным жанрам.")
        self._model = QStringListModel(path_suggestions())
        self.comp = QCompleter(self._model, self)
        self.comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.comp.setFilterMode(Qt.MatchFlag.MatchContains)
        self.comp.setWidget(self)
        self.comp.activated[str].connect(self._insert)
        self.textEdited.connect(self._suggest)

    def set_known(self, genres: list[str]) -> None:
        """The library's genres outside the tree are offered too."""
        self._model.setStringList(path_suggestions(genres))

    def _span(self) -> tuple[int, int]:
        """Start and end of the item under the cursor."""
        text, pos = self.text(), self.cursorPosition()
        start = max(text.rfind(c, 0, pos) for c in SEPARATORS) + 1
        ends = [i for i in (text.find(c, pos) for c in SEPARATORS) if i >= 0]
        return start, min(ends) if ends else len(text)

    def _suggest(self, _text: str) -> None:
        start, end = self._span()
        item = self.text()[start:end].strip()
        if not item:
            self.comp.popup().hide()
            return
        self.comp.setCompletionPrefix(item)
        if self.comp.completionCount():
            self.comp.complete()
        else:
            self.comp.popup().hide()

    def _insert(self, chosen: str) -> None:
        start, end = self._span()
        text = self.text()
        lead = " " if start > 0 else ""
        new = text[:start] + lead + chosen + text[end:]
        self.setText(new)
        self.setCursorPosition(start + len(lead) + len(chosen))

    def keyPressEvent(self, e) -> None:
        popup = self.comp.popup()
        if popup.isVisible() and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
            idx = popup.currentIndex()
            if idx.isValid():
                self._insert(idx.data())
                popup.hide()
                return
            popup.hide()
            if e.key() == Qt.Key.Key_Tab:
                return
        if e.key() == Qt.Key.Key_Escape and popup.isVisible():
            popup.hide()
            return
        super().keyPressEvent(e)
