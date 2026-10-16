"""The common labels window: moods, recording type, language / vocals, genres and era - for the playing track, or
for a whole album / artist (every click applies to all their tracks).  Every click is saved at once (and the library /
wave pages refresh)."""

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
        self.tids: list[int] = []                     # one track, or every track of an album / artist
        self.group = ""                               # «Альбом «…»» / «Исполнитель «…»» when labelling a group
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
        self.chips.toggled.connect(lambda m, on: self._apply(lambda: ctx.db.set_moods(self.tids, [m], "add" if on else "remove")))
        self.flags.toggled.connect(lambda f, on: self._apply(lambda: ctx.db.set_flags(self.tids, [f], "add" if on else "remove")))
        self.lv.lang_chosen.connect(lambda x: self._apply(lambda: ctx.db.set_language(self.tids, x, src="manual")))
        self.lv.vocal_chosen.connect(lambda x: self._apply(lambda: ctx.db.set_vocal(self.tids, x, src="manual")))
        self.genres.toggled.connect(lambda g, on: self._apply(lambda: ctx.db.set_genres(self.tids, [g], "add" if on else "remove")))
        self.era.chosen.connect(lambda d: self._apply(lambda: ctx.db.set_era(self.tids, d)))
        self.btn_trash.clicked.connect(self._trash)
        self.btn_copy.clicked.connect(self._copy)
        from PySide6.QtGui import QKeySequence, QShortcut
        QShortcut(QKeySequence("Delete"), self, activated=self._trash)     # Del: the playing track -> Recycle Bin
        self.btn_new_mood.clicked.connect(self.new_mood)
        self.btn_paste.clicked.connect(self._paste)
        ctx.events.tracks_changed.connect(self._external)

    @property
    def tid(self) -> int | None:
        """The single track (copy / paste / delete work on one track)."""
        return self.tids[0] if len(self.tids) == 1 else None

    def _apply(self, fn) -> None:
        if not self.tids:
            return
        fn()
        self.ctx.db.conn.commit()
        self.ctx.events.tracks_changed.emit(list(self.tids))

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
        if self.tids:
            self.ctx.db.set_moods(self.tids, [name], "add")
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
        if self.tids and self.ctx.label_clipboard:
            self._apply(lambda: labelclip.paste(self.ctx.db, self.tids, self.ctx.label_clipboard))
            self._refresh()

    def _trash(self) -> None:
        t = self.ctx.db.get_track(self.tid) if self.tid is not None else None
        if t and confirm_trash(self, self.ctx, [t]):
            self.hide()

    def _external(self, ids) -> None:
        if set(self.tids) & set(ids) and self.isVisible():
            self._refresh()

    def _refresh(self) -> None:
        tracks = [t for t in (self.ctx.db.get_track(i) for i in self.tids) if t]
        t = tracks[0] if len(tracks) == 1 else None
        names = [m["name"] for m in self.ctx.db.list_moods()]
        if self.chips.needs_update(names):
            self.chips.set_moods(names)
        if self.group:
            self.title.setText(f"{self.group} — треков: {len(tracks)} (метка ставится всем; полузакрашенная — есть у части)")
        else:
            self.title.setText(f"{t['artist']} — {t['title']}" if t else "Ничего не играет")
        self.chips.set_counts(self.ctx.db.mood_example_counts())
        self.chips.show_state(tracks)
        self.flags.show_state(tracks)
        self.lv.show_state(tracks)
        from musicplayer.core import labelclip
        clip = self.ctx.label_clipboard
        self.btn_copy.setEnabled(t is not None)
        self.btn_paste.setEnabled(bool(tracks) and clip is not None)
        self.btn_paste.setToolTip(labelclip.describe(clip))
        self.btn_trash.setVisible(not self.group)          # a whole album is never deleted from here
        self.genres.show_state(tracks)
        self.era.show_state(tracks)

    def open_for(self, track: dict | None, anchor) -> None:
        self.open_for_tracks([track] if track else [], anchor)

    def open_for_tracks(self, tracks: list[dict], anchor, group: str = "") -> None:
        """Label several tracks at once (an album, an artist); ``group`` names them in the title."""
        self.tids = [t["id"] for t in tracks if t]
        self.group = group
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


def group_tracks(db, t: dict) -> tuple[list[dict], list[dict]]:
    """(album tracks, artist tracks) of the track: its own album / everything of its (album) artist, feat excluded."""
    artist = (t.get("albumartist") or t.get("artist") or "")
    own = lambda r: (r.get("albumartist") or r.get("artist") or "").casefold() == artist.casefold()   # noqa: E731
    by_artist = [r for r in db.search(artist=artist, order="album", limit=10**6)] if artist else []
    by_artist = [r for r in by_artist if own(r)]
    album = [r for r in by_artist if (r.get("album") or "") == (t.get("album") or "")] if t.get("album") else []
    return album, by_artist


def labels_menu(ctx, button, popup: TagPopup) -> None:
    """The tag button by the like: labels of the track, of its album or of its artist - in the one labels window."""
    from PySide6.QtWidgets import QMenu
    from musicplayer.ui.hideactions import show_menu
    t = ctx.player.current
    if t is None:
        return
    album, by_artist = group_tracks(ctx.db, t)
    artist = t.get("albumartist") or t.get("artist") or ""
    m = QMenu(button)
    m.addSection("Метки")
    m.addAction(f"трека «{t['title']}»…", lambda: popup.open_for(t, button))
    if len(album) > 1:
        m.addAction(f"альбома «{t['album']}» ({len(album)})…",
                    lambda: popup.open_for_tracks(album, button, f"Альбом «{t['album']}»"))
    if len(by_artist) > 1:
        m.addAction(f"исполнителя «{artist}» ({len(by_artist)})…",
                    lambda: popup.open_for_tracks(by_artist, button, f"Исполнитель «{artist}»"))
    show_menu(m, button.mapToGlobal(button.rect().bottomLeft()))
