"""Quick labelling of the track that is playing: the same mood / recording-type chips as the wave and the library,
in a popup next to the player bar.  Every click is saved at once (and the library / wave pages refresh)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from musicplayer.ui.context import AppContext
from musicplayer.ui.pages.library import TRASH_CSS, EraChips, FlagChips, GenreChips, LangVocalChips, MoodChips, confirm_trash
from musicplayer.ui.theme import TOKENS


class TagPopup(QFrame):
    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Popup)
        self.ctx = ctx
        self.tid: int | None = None
        self.setObjectName("tagpopup")
        self.setStyleSheet(f"QFrame#tagpopup {{ background:{TOKENS['surface']}; border:1px solid {TOKENS['border']}; }}")
        self.title = QLabel()
        self.title.setStyleSheet("font-weight:600;")
        self.chips = MoodChips()
        self.flags = FlagChips()
        self.lv = LangVocalChips()
        self.genres = GenreChips()
        self.era = EraChips()
        self.btn_trash = QPushButton("🗑 Удалить файл…  (Del)")
        self.btn_trash.setStyleSheet(TRASH_CSS)
        lay = QVBoxLayout(self)
        lay.setSpacing(4)
        head = QHBoxLayout()                           # title, and the delete button apart in the corner
        head.addWidget(self.title, 1)
        self.btn_new_mood = QPushButton("＋ Настроение")
        self.btn_new_mood.setToolTip("Новое настроение: появится в списке и сразу встанет этому треку")
        self.btn_new_mood.setStyleSheet("QPushButton { padding:2px 10px; font-size:12px; }")
        head.addWidget(self.btn_new_mood)
        self.btn_copy = QPushButton("⧉ Копировать")
        self.btn_copy.setToolTip("Скопировать все метки этого трека")
        self.btn_paste = QPushButton("⎘ Вставить")
        for b in (self.btn_copy, self.btn_paste):
            b.setStyleSheet("QPushButton { padding:2px 10px; font-size:12px; }")
            head.addWidget(b)
        head.addSpacing(16)
        head.addWidget(self.btn_trash)
        lay.addLayout(head)
        v = lay
        v.addWidget(self.chips)
        v.addWidget(self.flags)
        v.addWidget(self.lv)
        v.addWidget(self.genres)
        v.addWidget(self.era)
        self.chips.toggled.connect(lambda m, on: self._apply(lambda: ctx.db.set_moods([self.tid], [m], "add" if on else "remove")))
        self.flags.toggled.connect(lambda f, on: self._apply(lambda: ctx.db.set_flags([self.tid], [f], "add" if on else "remove")))
        self.lv.lang_chosen.connect(lambda x: self._apply(lambda: ctx.db.set_language([self.tid], x, src="manual")))
        self.lv.vocal_chosen.connect(lambda x: self._apply(lambda: ctx.db.set_vocal([self.tid], x, src="manual")))
        self.genres.toggled.connect(lambda g, on: self._apply(lambda: ctx.db.set_genres([self.tid], [g], "add" if on else "remove")))
        self.era.chosen.connect(lambda d: self._apply(lambda: ctx.db.set_era([self.tid], d)))
        self.btn_trash.clicked.connect(self._trash)
        self.btn_copy.clicked.connect(self._copy)
        from PySide6.QtGui import QKeySequence, QShortcut
        QShortcut(QKeySequence("Delete"), self, activated=self._trash)     # Del: the playing track -> Recycle Bin
        self.btn_new_mood.clicked.connect(self.new_mood)
        self.btn_paste.clicked.connect(self._paste)
        ctx.events.tracks_changed.connect(self._external)

    def _apply(self, fn) -> None:
        if self.tid is None:
            return
        fn()
        self.ctx.events.tracks_changed.emit([self.tid])

    def new_mood(self, name: str | None = None) -> None:
        from PySide6.QtWidgets import QInputDialog
        if name is None:
            name, ok = QInputDialog.getText(self, "Новое настроение", "Название:")
            if not ok:
                return
        name = (name or "").strip()
        if not name:
            return
        self.ctx.db.add_mood(name)
        if self.tid is not None:
            self.ctx.db.set_moods([self.tid], [name], "add")
        self.ctx.db.conn.commit()
        self.ctx.events.library_changed.emit()
        self._apply(lambda: None)
        self._refresh()

    def _copy(self) -> None:
        from musicplayer.core import labelclip
        if self.tid is not None:
            self.ctx.label_clipboard = labelclip.snapshot(self.ctx.db, self.tid)
            self._refresh()

    def _paste(self) -> None:
        from musicplayer.core import labelclip
        if self.tid is not None and self.ctx.label_clipboard:
            self._apply(lambda: labelclip.paste(self.ctx.db, [self.tid], self.ctx.label_clipboard))
            self._refresh()

    def _trash(self) -> None:
        t = self.ctx.db.get_track(self.tid) if self.tid is not None else None
        if t and confirm_trash(self, self.ctx, [t]):
            self.hide()

    def _external(self, ids) -> None:
        if self.tid in ids and self.isVisible():
            self._refresh()

    def _refresh(self) -> None:
        t = self.ctx.db.get_track(self.tid) if self.tid is not None else None
        names = [m["name"] for m in self.ctx.db.list_moods()]
        if self.chips.needs_update(names):
            self.chips.set_moods(names)
        self.title.setText(f"{t['artist']} — {t['title']}" if t else "Ничего не играет")
        self.chips.set_counts(self.ctx.db.mood_example_counts())
        self.chips.show_state([t] if t else [])
        self.flags.show_state([t] if t else [])
        self.lv.show_state([t] if t else [])
        from musicplayer.core import labelclip
        clip = self.ctx.label_clipboard
        self.btn_copy.setEnabled(t is not None)
        self.btn_paste.setEnabled(t is not None and clip is not None)
        self.btn_paste.setToolTip(labelclip.describe(clip))
        self.genres.show_state([t] if t else [])
        self.era.show_state([t] if t else [])

    def open_for(self, track: dict | None, anchor) -> None:
        self.tid = track["id"] if track else None
        self._refresh()
        # everything on screen, no scrolling: the width fits the screen, the height follows the wrapped chips
        scr = anchor.screen().availableGeometry()
        w = min(760, scr.width() - 16)
        self.setFixedWidth(w)
        self.show()
        self.layout().activate()
        h = min(self.layout().heightForWidth(w) if self.layout().hasHeightForWidth() else self.sizeHint().height(),
                scr.height() - 16)
        self.setFixedHeight(max(h, self.sizeHint().height() if h <= 0 else h))
        pos = anchor.mapToGlobal(anchor.rect().topRight())
        x = min(max(scr.left() + 8, pos.x() - w), scr.right() - w - 8)
        y = max(scr.top() + 8, pos.y() - self.height() - 6)
        self.move(x, y)
