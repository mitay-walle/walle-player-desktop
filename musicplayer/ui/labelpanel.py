"""Labels of the playing track right on «Моя волна»: moods, recording type, language / vocals, genres, era.

Clicks only prepare the change (a draft); «К треку», «К альбому (N)» or «К исполнителю (N)» apply it, «Отмена» brings
back what the track has.  For an album / artist every chosen label is added to all its tracks and every label taken off
here is removed from them (labels nobody touched stay as they are on each track).  «Удалить трек…» sends the file to the
Recycle Bin (asks first)."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from musicplayer.ui.pages.library import TRASH_CSS, EraChips, FlagChips, GenreChips, LangVocalChips, MoodChips
from musicplayer.ui.theme import TOKENS


class LabelPanel(QWidget):
    applied = Signal(str)                    # a message for the page («Метки — альбому «…» (12)»)

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.track: dict | None = None
        self.orig: dict = {}
        self.draft: dict = {}
        self.head = QLabel("Метки трека")
        self.head.setStyleSheet("font-weight:600;")
        self.moods = MoodChips()
        self.flags = FlagChips()
        self.lv = LangVocalChips()
        self.genres = GenreChips()
        self.era = EraChips()
        self.btn_track = QPushButton("К треку")
        self.btn_track.setObjectName("Primary")
        self.btn_track.setToolTip("Поставить отмеченное этому треку")
        self.btn_album = QPushButton("К альбому")
        self.btn_album.setToolTip("Отмеченное — всем трекам альбома, снятое — снять у всех")
        self.btn_artist = QPushButton("К исполнителю")
        self.btn_artist.setToolTip("Отмеченное — всем трекам исполнителя, снятое — снять у всех")
        self.btn_cancel = QPushButton("Отмена")
        self.btn_cancel.setToolTip("Вернуть метки, какие у трека сейчас")
        self.btn_delete = QPushButton("Удалить трек…")
        self.btn_delete.setStyleSheet(TRASH_CSS)
        self.btn_delete.setToolTip("Файл — в корзину Windows (спросит перед удалением)")
        self.state = QLabel()
        self.state.setStyleSheet(f"color:{TOKENS['text_muted']}; font-size:12px;")
        row = QHBoxLayout()
        for b in (self.btn_track, self.btn_album, self.btn_artist, self.btn_cancel):
            row.addWidget(b)
        row.addWidget(self.state, 1)
        row.addWidget(self.btn_delete)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self.head)
        for w in (self.moods, self.flags, self.lv, self.genres, self.era):
            lay.addWidget(w)
        lay.addLayout(row)

        self.moods.toggled.connect(lambda m, on: self._toggle("moods", m, on))
        self.flags.toggled.connect(lambda f, on: self._toggle("flags", f, on))
        self.genres.toggled.connect(lambda g, on: self._toggle("genres", g, on))
        self.lv.lang_chosen.connect(lambda x: self._set("lang", x))
        self.lv.vocal_chosen.connect(lambda x: self._set("vocal", x))
        self.era.chosen.connect(lambda d: self._set("era", d))
        self.btn_track.clicked.connect(lambda: self.apply("track"))
        self.btn_album.clicked.connect(lambda: self.apply("album"))
        self.btn_artist.clicked.connect(lambda: self.apply("artist"))
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_delete.clicked.connect(self.delete)
        self.load(None)

    # ---- the draft ------------------------------------------------------------------------------------
    @staticmethod
    def _state(t: dict | None) -> dict:
        if not t:
            return {}
        return {"moods": set(t.get("moods", [])), "flags": set(t.get("flags", [])), "genres": set(t.get("genres", [])),
                "lang": t.get("lang"), "vocal": t.get("vocal"), "era": t.get("era"), "year": t.get("year")}

    def load(self, t: dict | None) -> None:
        """Show the track's labels (a fresh draft)."""
        self.track = self.ctx.db.get_track(t["id"]) if t else None
        self.orig = self._state(self.track)
        self.draft = {k: (set(v) if isinstance(v, set) else v) for k, v in self.orig.items()}
        names = [m["name"] for m in self.ctx.db.list_moods()]
        if self.moods.needs_update(names):
            self.moods.set_moods(names)
        self._render()

    def _toggle(self, key: str, name: str, on: bool) -> None:
        if self.track is None:
            return
        (self.draft[key].add if on else self.draft[key].discard)(name)
        self._render()

    def _set(self, key: str, value) -> None:
        if self.track is not None:
            self.draft[key] = value
            self._render()

    def dirty(self) -> bool:
        return self.draft != self.orig

    def _render(self) -> None:
        d = self.draft
        shown = [{**d, "moods": sorted(d["moods"]), "flags": sorted(d["flags"]), "genres": sorted(d["genres"])}] if d else []
        for w in (self.moods, self.flags, self.lv, self.genres, self.era):
            w.show_state(shown)
        t = self.track
        self.head.setText(f"Метки трека «{t['title']}»" if t else "Метки трека — ничего не играет")
        album, by_artist = self._groups()
        self.btn_album.setText(f"К альбому ({len(album)})" if album else "К альбому")
        self.btn_artist.setText(f"К исполнителю ({len(by_artist)})" if by_artist else "К исполнителю")
        self.btn_album.setEnabled(len(album) > 0)
        self.btn_artist.setEnabled(len(by_artist) > 0)
        self.btn_track.setEnabled(t is not None and self.dirty())
        self.btn_cancel.setEnabled(t is not None and self.dirty())
        self.btn_delete.setEnabled(t is not None)
        self.state.setText("есть неприменённые изменения" if t is not None and self.dirty() else "")

    def _groups(self) -> tuple[list[dict], list[dict]]:
        if self.track is None:
            return [], []
        from musicplayer.ui.tagpopup import group_tracks
        if getattr(self, "_groups_for", None) != self.track["id"]:
            self._groups_for, self._groups_cache = self.track["id"], group_tracks(self.ctx.db, self.track)
        return self._groups_cache

    # ---- actions -----------------------------------------------------------------------------------------
    def apply(self, scope: str) -> None:
        """track: exactly the draft; album / artist: chosen labels added to all, labels taken off here removed."""
        if self.track is None:
            return
        album, by_artist = self._groups()
        tracks = {"track": [self.track], "album": album, "artist": by_artist}[scope]
        ids = [t["id"] for t in tracks] or [self.track["id"]]
        db, d, o = self.ctx.db, self.draft, self.orig
        if scope == "track":
            db.set_moods(ids, sorted(d["moods"]), "set")
            db.set_genres(ids, sorted(d["genres"]), "set")
            db.set_flags(ids, sorted(d["flags"]), "set")
        else:
            for key, setter in (("moods", db.set_moods), ("genres", db.set_genres), ("flags", db.set_flags)):
                if d[key]:
                    setter(ids, sorted(d[key]), "add")
                gone = o[key] - d[key]
                if gone:
                    setter(ids, sorted(gone), "remove")
        if scope == "track" or d["lang"] != o["lang"]:
            db.set_language(ids, d["lang"], src="manual")
        if scope == "track" or d["vocal"] != o["vocal"]:
            db.set_vocal(ids, d["vocal"], src="manual")
        if d["era"] != o["era"]:
            db.set_era(ids, d["era"])
        db.conn.commit()
        self.ctx.events.tracks_changed.emit(ids)
        what = {"track": "треку", "album": f"альбому «{self.track.get('album')}»", "artist": "исполнителю"}[scope]
        self.applied.emit(f"Метки применены к {what}" + (f" ({len(ids)})" if scope != "track" else ""))
        self.load(self.track)

    def cancel(self) -> None:
        self.load(self.track)

    def delete(self) -> None:
        from musicplayer.ui.pages.library import confirm_trash
        if self.track is not None and confirm_trash(self, self.ctx, [self.track]):
            self.load(self.ctx.player.current)
