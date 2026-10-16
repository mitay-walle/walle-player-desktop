"""'My wave' (the home page): pick a mood, press play, listen endlessly - and rate tracks to teach the model.

Style follows Yandex Music's 'My Wave': one big play button, the mood/character choices around it, 👍/👎 while
listening.  Tracks you confirmed for the mood are mixed with tracks the model only *guesses* fit; rating a guess
👍 confirms the mood, 👎 rejects it - listening labels your library without a separate labelling session.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from musicplayer.ui.widgets import FlowLayout
from musicplayer.core.wave import CHARACTERS, Wave
from musicplayer.ui.context import AppContext
from musicplayer.ui.pages.library import GenreChips, MoodChips
from musicplayer.ui.theme import TOKENS
from musicplayer.ui.widgets import CoverCache

REFILL_BELOW = 3
BATCH = 10


class WavePage(QWidget):
    key = "wave"

    def __init__(self, ctx: AppContext, covers: CoverCache, parent=None) -> None:
        super().__init__(parent)
        self.ctx, self.covers = ctx, covers
        self.wave: Wave | None = None
        self.active = False
        self.character = "mixed"
        self.zoom = 1.0
        self.rated_id: int | None = None          # track already rated: thumbs stay disabled until the next track
        self.selected: set[str] = set()

        self.heading = QLabel("Моя волна")
        self.heading.setStyleSheet("font-size:26px; font-weight:700;")
        self.chips = MoodChips()
        self.genre_chips = GenreChips()             # families; a chosen family opens its sub-genres
        self.genre_chips.set_selected(())
        self.char_buttons: dict[str, QPushButton] = {}
        chars_holder = QWidget()
        chars_holder.setObjectName("segmented")
        chars_holder.setStyleSheet("QWidget#segmented { background:#18181b; border:1px solid #2c2c31; border-radius:20px; }")
        chars = QHBoxLayout(chars_holder)
        chars.setContentsMargins(4, 4, 4, 4)
        chars.setSpacing(4)
        for key, label in CHARACTERS.items():
            b = QPushButton(label)
            b.setCheckable(True)
            b.setStyleSheet("QPushButton { background:transparent; color:#92928F; border:none; border-radius:16px; padding:7px 16px; }"
                            "QPushButton:hover { color:#E6E3DA; }"
                            "QPushButton:checked { background:#2a2a2f; color:#E6E3DA; font-weight:600; }")
            b.setChecked(key == self.character)
            b.clicked.connect(lambda _=False, k=key: self.set_character(k))
            chars.addWidget(b)
            self.char_buttons[key] = b

        self.cover = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel()
        self.title.setStyleSheet("font-size:18px; font-weight:600;")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setWordWrap(True)
        self.sub = QLabel()
        self.sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub.setStyleSheet(f"color:{TOKENS['text_muted']}")
        for w in (self.title, self.sub):                 # the playing track's text can be selected and copied
            w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            w.setCursor(Qt.CursorShape.IBeamCursor)
        self.badge = QLabel()
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setWordWrap(True)
        self.toast = QLabel()
        self.toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.toast.setStyleSheet(f"color:{TOKENS['success']}; font-size:13px;")
        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet(f"color:{TOKENS['text_muted']}; font-size:14px;")
        self.empty_btn = QPushButton()

        self.btn_dislike = QPushButton("👎")
        self.btn_play = QPushButton()
        self.btn_like = QPushButton("👍")
        self.btn_next = QPushButton("▶▶")
        self.btn_other = QPushButton()
        self.btn_other.setToolTip("Разметить трек: настроения, тип записи, язык, вокал, жанры, эпоха")
        self.btn_hide = QPushButton()
        self.btn_play.setFixedSize(72, 72)
        from PySide6.QtWidgets import QGraphicsDropShadowEffect
        from PySide6.QtGui import QColor as _QC
        glow = QGraphicsDropShadowEffect(self.btn_play)
        glow.setBlurRadius(42)
        glow.setOffset(0, 8)
        glow.setColor(_QC(213, 165, 44, 90))
        self.btn_play.setGraphicsEffect(glow)
        from PySide6.QtCore import QSize as _QS
        self.btn_play.setIconSize(_QS(34, 34))
        self.btn_play.setStyleSheet(f"QPushButton {{ background:{TOKENS['accent']}; color:{TOKENS['bg']}; border-radius:36px;"
                                    f"font-size:1px; padding:0; border:none; min-height:72px; max-height:72px; min-width:72px; max-width:72px; }}"
                                    f"QPushButton:hover {{ background:{TOKENS['accent_hover'] if 'accent_hover' in TOKENS else TOKENS['accent']}; }}")
        from musicplayer.ui.icons import icon, make_icon_button
        for b, name, tip in ((self.btn_dislike, "thumb_down", "Не нравится / модель ошиблась"),
                             (self.btn_like, "thumb_up", "Нравится / модель права"),
                             (self.btn_next, "next", "Следующий"), (self.btn_other, "tag", self.btn_other.toolTip()),
                             (self.btn_hide, "hide", "Не показывать: этот трек, альбом или исполнителя")):
            make_icon_button(b, name, TOKENS["text"], 30, tip)
            b.setFixedSize(56, 56)
        self._icon = icon
        controls = QHBoxLayout()
        controls.addStretch(1)
        for b in (self.btn_hide, self.btn_dislike, self.btn_other, self.btn_play, self.btn_like, self.btn_next):
            controls.addWidget(b)
        controls.addStretch(1)

        # panel: "the guess is wrong - which mood fits?"
        self.fix_box = QWidget()
        fl = QVBoxLayout(self.fix_box)
        fl.setContentsMargins(0, 6, 0, 0)
        self.fix_title = QLabel("Какое настроение подходит этому треку? (можно несколько)")
        self.fix_title.setStyleSheet("font-weight:600;")
        self.fix_title.setWordWrap(True)
        self.fix_chips = MoodChips()
        self.btn_fix_save = QPushButton("✓ Сохранить мой выбор")
        self.btn_fix_skip = QPushButton("Подходящего нет — пропустить")
        self.btn_fix_cancel = QPushButton("Отмена")
        fr = QHBoxLayout()
        for b in (self.btn_fix_save, self.btn_fix_skip, self.btn_fix_cancel):
            fr.addWidget(b)
        fl.addWidget(self.fix_title)
        fl.addWidget(self.fix_chips)
        fl.addLayout(fr)
        self.fix_box.hide()
        self.fix_selected: set[str] = set()

        col = QVBoxLayout()
        col.addWidget(self.heading)
        hint = QLabel("НАСТРОЕНИЕ — можно несколько; без выбора — любое из размеченных")
        hint.setStyleSheet("color:#92928F; font-size:10px; letter-spacing:1px;")
        hint.setWordWrap(True)
        col.addWidget(hint)
        col.addWidget(self.chips)
        ghint = QLabel("ЖАНР — без выбора любой; поджанр сужает семейство")
        ghint.setStyleSheet("color:#92928F; font-size:10px; letter-spacing:1px;")
        col.addWidget(ghint)
        col.addWidget(self.genre_chips)
        col.addWidget(chars_holder)
        col.addStretch(1)
        col.addWidget(self.cover)
        col.addWidget(self.title)
        col.addWidget(self.sub)
        col.addWidget(self.badge)
        col.addWidget(self.empty)
        col.addWidget(self.empty_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        col.addLayout(controls)
        col.addWidget(self.fix_box)
        col.addWidget(self.toast)
        from musicplayer.ui.labelpanel import LabelPanel
        self.labels = LabelPanel(ctx)                    # the playing track's labels right here (apply to track / album / artist)
        self.labels.applied.connect(self.toast.setText)
        col.addWidget(self.labels)
        col.addStretch(2)
        inner = QWidget()
        lay = QHBoxLayout(inner)
        lay.addStretch(1)
        wrap = QWidget()
        wrap.setMaximumWidth(1000)
        wrap.setLayout(col)
        lay.addWidget(wrap, 4)
        lay.addStretch(1)
        scroll = QScrollArea()                           # the labels make the page tall: it scrolls on small windows
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.chips.toggled.connect(self._mood_toggled)
        self._genre_timer = QTimer(self, singleShot=True, interval=0)   # a sub-genre click also lights its family
        self._genre_timer.timeout.connect(self._retarget)
        self.genre_chips.toggled.connect(lambda *_: self._genre_timer.start())
        self.btn_play.clicked.connect(self.play_pressed)
        self.btn_like.clicked.connect(lambda: self.rate(True))
        self.btn_dislike.clicked.connect(lambda: self.rate(False))
        self.btn_next.clicked.connect(self.skip)
        self.btn_other.clicked.connect(self.labels_menu)
        from musicplayer.ui.hideactions import current_track_menu
        self.btn_hide.clicked.connect(lambda: current_track_menu(ctx, self.btn_hide))
        self.fix_chips.toggled.connect(self._fix_toggled)
        self.btn_fix_save.clicked.connect(self.save_fix)
        self.btn_fix_skip.clicked.connect(self.reject_and_skip)
        self.btn_fix_cancel.clicked.connect(self.close_fix)
        self.empty_btn.clicked.connect(self._empty_action)
        ctx.player.track_changed.connect(self._on_track)
        ctx.player.track_changed.connect(self.labels.load)
        ctx.events.tracks_changed.connect(self._labels_changed_elsewhere)
        self.suspended: dict | None = None
        self._last: dict | None = None
        self._remember_timer = QTimer(self, interval=1000)
        self._remember_timer.timeout.connect(self._remember)
        self._remember_timer.start()
        ctx.player.state_changed.connect(lambda s: self._render())
        ctx.events.library_changed.connect(self.reload)
        ctx.events.tracks_removed.connect(lambda ids: self.reload())
        ctx.events.hidden_changed.connect(self._hidden_changed)
        from musicplayer.ui.hideactions import chip_menu

        def genre_menus() -> None:
            for g, b in self.genre_chips.buttons.items():
                chip_menu(b, ctx, "genre", g)
        genre_menus()
        self.genre_chips.rebuilt.connect(genre_menus)
        ctx.events.tracks_changed.connect(lambda ids: self.genre_chips.refresh_tree())
        ctx.events.tracks_changed.connect(lambda ids: self._render())
        self._empty_target = ""
        self.reload()

    # ---- state --------------------------------------------------------
    def reload(self) -> None:
        names = [m["name"] for m in self.ctx.db.list_moods()]
        if self.chips.needs_update(names):
            self.chips.set_moods(names)
            from musicplayer.ui.hideactions import chip_menu
            for n, b in self.chips.buttons.items():
                chip_menu(b, self.ctx, "mood", n)
        self.chips.set_counts(self.ctx.db.mood_example_counts())
        self._hide_chips()
        self._render()

    def _hide_chips(self) -> None:
        """Genres and labels the chosen listener hid are not offered (and leave the current choice)."""
        from musicplayer.core import hidden
        h = hidden.keys(self.ctx.db, self.ctx.listener)
        for n, b in self.chips.buttons.items():
            b.setVisible(n.casefold() not in h["mood"])
        self.genre_chips.set_hidden(h["genre"])
        moods = {m for m in self.selected if m.casefold() not in h["mood"]}
        genres = {g for g in self.genre_chips.selected() if g.casefold() not in h["genre"]}
        if moods != self.selected or genres != self.genre_chips.selected():
            self.selected = moods
            self.genre_chips.set_selected(genres)
            self.genre_chips.set_hidden(h["genre"])
            self._retarget()

    def _labels_changed_elsewhere(self, ids) -> None:
        """Labels changed in another window: the panel shows them, unless it holds changes not applied yet."""
        t = self.labels.track
        if t is not None and (not ids or t["id"] in ids) and not self.labels.dirty():
            self.labels.load(t)

    def _hidden_changed(self) -> None:
        """«Не показывать» while the wave plays: the coming tracks follow at once (the current one plays on)."""
        self._hide_chips()
        if self.active:
            self.wave = self._make_wave()
            self.ctx.player.drop_upcoming()
            self._refill()

    def _counts(self) -> tuple[int, int]:
        c = self.ctx.db.conn
        tracks = c.execute("SELECT COUNT(*) FROM tracks WHERE missing=0").fetchone()[0]
        labelled = c.execute("SELECT COUNT(DISTINCT track_id) FROM track_moods").fetchone()[0]
        return tracks, labelled

    def set_character(self, key: str) -> None:
        self.character = key
        for k, b in self.char_buttons.items():
            b.setChecked(k == key)
        self._retarget()

    def _mood_toggled(self, name: str, wanted: bool) -> None:
        (self.selected.add if wanted else self.selected.discard)(name)
        self._retarget()

    def select_moods(self, names: list[str]) -> None:
        self.selected = set(names)
        self._retarget()

    def _retarget(self) -> None:
        """Mood/character changed: later tracks follow the new choice, the current song keeps playing."""
        w = self.window()
        if hasattr(w, "save_session"):
            w.save_session()                         # the choice is kept at once (a crash or kill does not lose it)
        if self.active:
            self.wave = self._make_wave()
            self.ctx.player.drop_upcoming()
            self._refill()
        self._render()

    def wave_genres(self) -> list[str]:
        """What the wave filters by: a family, or only its chosen sub-genres when some are lit."""
        sel = self.genre_chips.selected()
        tree = self.genre_chips.tree
        out = []
        for g in sorted(sel):
            subs = [s for s in tree.get(g, []) if s in sel]
            if g in tree:
                out += subs or [g]
            elif self.genre_chips.parent_of.get(g) not in sel:
                out.append(g)
        return sorted(set(out))

    def _make_wave(self) -> Wave:
        return Wave(self.ctx.db, sorted(self.selected), self.character, genres=self.wave_genres(), user=self.ctx.listener)

    # ---- playback -----------------------------------------------------
    def start(self, moods: list[str] | None = None, character: str | None = None) -> int:
        if moods is not None:
            self.selected = set(moods)
        if character:
            self.character = character
            for k, b in self.char_buttons.items():
                b.setChecked(k == character)
        self.wave = self._make_wave()
        batch = self.wave.next_batch(BATCH)
        if not batch:
            self.active = False
            self.toast.setText("Нет подходящих треков: снимите часть настроений или жанров, или разметьте ещё треки "
                               "(вкладка «Разметка»)." if self.selected or self.wave_genres() else
                               "Нет подходящих треков: разметьте несколько настроением (вкладка «Разметка»).")
            self._render()
            return 0
        self.active = True
        self.toast.setText("")
        self.ctx.player.set_shuffle("off")
        self.ctx.player.set_repeat("off")
        self.ctx.player.play_tracks(batch)
        self._render()
        return len(batch)

    # ---- session (survives a restart) ------------------------------------------------
    def session_state(self, queue_items: list[dict]) -> dict:
        meta = {str(t["id"]): [t["wave_kind"], t.get("wave_moods", []), t.get("wave_conf")]
                for t in queue_items if "wave_kind" in t}
        susp = self.suspended
        if susp:
            susp = {**susp, "items": [t["id"] for t in susp["items"]],
                    "meta": {str(t["id"]): [t["wave_kind"], t.get("wave_moods", []), t.get("wave_conf")] for t in susp["items"]}}
        return {"active": bool(self.active), "moods": sorted(self.selected), "character": self.character, "meta": meta,
                "genres": sorted(self.genre_chips.selected()), "suspended": susp}

    def restore_session(self, state: dict | None, tracks: list[dict]) -> list[dict]:
        """Restore the chosen moods / character; when the wave was playing, mark the restored queue as wave tracks."""
        if not state:
            return tracks
        names = set(self.chips.buttons)
        self.selected = {m for m in state.get("moods", []) if m in names}
        self.character = state.get("character") or self.character
        for k, b in self.char_buttons.items():
            b.setChecked(k == self.character)
        self.genre_chips.set_selected(state.get("genres", []))
        susp = state.get("suspended")
        if susp:
            items = []
            for tid in susp.get("items", []):
                t = self.ctx.db.get_track(tid)
                if t:
                    kind, moods, conf = susp.get("meta", {}).get(str(tid), ["known", [], None])
                    items.append({**t, "wave_kind": kind, "wave_moods": moods, **({"wave_conf": conf} if conf is not None else {})})
            if items:
                self.suspended = {**susp, "items": items}
        if state.get("active"):
            meta = state.get("meta", {})
            out = []
            for t in tracks:
                kind, moods, conf = meta.get(str(t["id"]), ["known", [], None])
                out.append({**t, "wave_kind": kind, "wave_moods": moods, **({"wave_conf": conf} if conf is not None else {})})
            tracks = out
            self.wave = self._make_wave()
            self.active = True
        self._render()
        return tracks

    def _remember(self) -> None:
        """While the wave plays: its queue, current track and position (to come back after other playback)."""
        p = self.ctx.player
        q = p.queue
        if self.active and p.current is not None and "wave_kind" in p.current and q.items:
            self._last = {"items": [t for t in q.items if "wave_kind" in t], "current": p.current["id"],
                          "pos": float(p.deck.player.position()) / 1000 if hasattr(p, "deck") else 0.0,
                          "moods": sorted(self.selected), "character": self.character, "genres": self.wave_genres()}

    def _suspend(self) -> None:
        if getattr(self, "_last", None):
            self.suspended = self._last

    def resume(self) -> bool:
        """Continue the interrupted wave at its track and second (same moods and character only)."""
        s = getattr(self, "suspended", None)
        if not s or s["moods"] != sorted(self.selected) or s["character"] != self.character                 or s.get("genres", []) != self.wave_genres():
            return False
        items = s["items"]
        idx = next((i for i, t in enumerate(items) if t["id"] == s["current"]), None)
        if idx is None or not hasattr(self.ctx.player, "cue"):
            return False
        self.suspended = None
        self.wave = self._make_wave()
        self.active = True
        self.ctx.player.set_shuffle("off")
        self.ctx.player.set_repeat("off")
        self.ctx.player.cue(items, idx, s["pos"])
        self.ctx.player.play()
        self._render()
        return True

    def play_pressed(self) -> None:
        p = self.ctx.player
        if self.active and p.current is not None:
            p.toggle()
        elif not self.resume():
            self.start()

    def _refill(self) -> None:
        q = self.ctx.player.queue
        left = len(q.order) - q.pos - 1 if q.pos >= 0 else 0
        if self.active and self.wave is not None and left < REFILL_BELOW:
            recent = {t["id"] for t in q.items[-60:]}
            more = self.wave.next_batch(BATCH, exclude=recent)
            if more:
                self.ctx.player.enqueue(more)

    def _on_track(self, t: dict | None) -> None:
        if self.active and t is not None and "wave_kind" not in t:
            self.active = False                      # something else (library, labelling feed) took over playback
            self._suspend()
        if self.active and t is not None:
            self._refill()
        elif self.active and t is None and self.ctx.player.state == "stopped":
            self.active = False
        self.toast.setText("")
        self.fix_box.hide()
        self._render()

    def rate(self, like: bool) -> None:
        cur = self.ctx.player.current
        if not (self.active and cur and self.wave and "wave_kind" in cur):
            return
        if self.rated_id == cur["id"]:
            return                                   # one rating per track
        if not like:                                 # "not this one": let the user say which mood fits instead
            self.open_fix()
            return
        self.rated_id = cur["id"]
        res = self.wave.like(cur)
        msgs = {"confirmed": "Запомнил: «{m}» — спасибо, модель станет точнее", "liked": "Учтено",
                "rejected": "Больше не предложу «{m}» для этого трека", "skipped": "Пропускаю"}
        self.toast.setText(msgs[res["result"]].format(m=", ".join(res["moods"])))
        if res["moods"]:
            self.ctx.events.tracks_changed.emit([cur["id"]])
        if not like:
            self.ctx.player.next()
        self._render_buttons()

    # ---- "the guess is wrong": pick the right moods ---------------------
    def _fixable(self) -> dict | None:
        cur = self.ctx.player.current
        return cur if (self.active and cur and "wave_kind" in cur) else None

    def labels_menu(self) -> None:
        """The tag button: labels of the track, its album or its artist (the same menu as in the player bar)."""
        from musicplayer.ui.tagpopup import TagPopup, labels_menu
        if not hasattr(self, "_tags"):
            self._tags = TagPopup(self.ctx, self)
        labels_menu(self.ctx, self.btn_other, self._tags)

    def open_tags(self) -> None:
        """✎: the full labelling popup (the same as «Метки» in the player bar) for the playing track."""
        from musicplayer.ui.tagpopup import TagPopup
        cur = self.ctx.player.current
        if cur is None:
            return
        if not hasattr(self, "_tags"):
            self._tags = TagPopup(self.ctx, self)
        self._tags.open_for(cur, self.btn_other)

    def open_fix(self) -> None:
        cur = self._fixable()
        if cur is None:
            return
        names = [m["name"] for m in self.ctx.db.list_moods()]
        if self.fix_chips.needs_update(names):
            self.fix_chips.set_moods(names)
        self.fix_chips.set_counts(self.ctx.db.mood_example_counts())
        self.fix_selected = set(self.ctx.db.get_track(cur["id"])["moods"]) - set(cur.get("wave_moods", [])) \
            if cur.get("wave_kind") in ("guess", "auto") else set(self.ctx.db.get_track(cur["id"])["moods"])
        self.fix_chips.show_state([{"moods": sorted(self.fix_selected)}])
        for b in self.fix_chips.buttons.values():
            b.setEnabled(True)
        self.fix_box.show()

    def _fix_toggled(self, name: str, wanted: bool) -> None:
        (self.fix_selected.add if wanted else self.fix_selected.discard)(name)

    def close_fix(self) -> None:
        self.fix_box.hide()

    def save_fix(self) -> None:
        """Store the user's own choice (manual labels); guessed moods that were not chosen count as rejected."""
        cur = self._fixable()
        if cur is None or not self.fix_selected:
            self.toast.setText("Отметьте хотя бы одно настроение или нажмите «Подходящего нет»")
            return
        db, tid = self.ctx.db, cur["id"]
        guessed = [m for m in cur.get("wave_moods", []) if m not in self.fix_selected]
        db.set_moods([tid], sorted(self.fix_selected), "set")
        for m in guessed:
            db.conn.execute("INSERT OR REPLACE INTO mood_rejects VALUES(?,?,?)", (tid, db.add_mood(m), time.time()))
        cur["wave_kind"], cur["wave_moods"] = "known", sorted(self.fix_selected)
        self.rated_id = tid
        self.fix_box.hide()
        self.toast.setText("Сохранено: " + ", ".join(sorted(self.fix_selected)) + " — спасибо, модель учтёт")
        self.ctx.events.tracks_changed.emit([tid])
        self._render()

    def reject_and_skip(self) -> None:
        """No mood fits: remember the rejection of the guess and move on."""
        cur = self._fixable()
        if cur is None:
            return
        self.rated_id = cur["id"]
        res = self.wave.dislike(cur)
        self.fix_box.hide()
        if res["moods"]:
            self.ctx.events.tracks_changed.emit([cur["id"]])
        self.ctx.player.next()

    def skip(self) -> None:
        if self.active:
            self.ctx.player.next()

    # ---- rendering ----------------------------------------------------
    def apply_zoom(self, f: float) -> None:
        self.zoom = f
        self._render()

    def _empty_action(self) -> None:
        if self._empty_target == "scan":
            self.ctx.events.request_scan.emit()
        elif self._empty_target:
            self.ctx.events.navigate.emit(self._empty_target)

    def _render_buttons(self) -> None:
        cur = self.ctx.player.current
        live = bool(self.active and cur and "wave_kind" in cur)
        guess = bool(live and cur.get("wave_kind") in ("guess", "auto"))
        rated = bool(live and self.rated_id == cur["id"])
        self.btn_next.setEnabled(live)
        self.btn_other.setEnabled(live)          # labels stay editable after rating
        self.btn_hide.setEnabled(live)
        for b, name in ((self.btn_like, "thumb_up"), (self.btn_dislike, "thumb_down")):
            b.setEnabled(live and not rated)
            b.setIcon(self._icon(name, TOKENS["text_dim"] if rated or not live else TOKENS["text"], 30))
        playing = self.ctx.player.state == "playing"
        if rated:
            self.btn_like.setToolTip("Уже оценено")
            self.btn_dislike.setToolTip("Уже оценено")
            self.btn_play.setIcon(self._icon("pause" if playing else "play", TOKENS["bg"], 34))
            return
        self.btn_like.setToolTip("Да, это настроение подходит" if guess else "Нравится")
        self.btn_dislike.setToolTip("Не подходит: больше не предлагать это настроение для трека" if guess else "Пропустить")
        self.btn_play.setIcon(self._icon("pause" if self.active and playing else "play", TOKENS["bg"], 34))

    def _render(self) -> None:
        tracks, labelled = self._counts()
        px = int(240 * self.zoom)
        cur = self.ctx.player.current if self.active else None
        if cur is not None and "wave_kind" not in cur:
            cur = None
        empty = ""
        if tracks == 0:
            empty, self._empty_target = "Библиотека пуста. Выберите папку с музыкой и просканируйте её.", "scan"
            self.empty_btn.setText("Выбрать папку и сканировать")
        elif labelled == 0:
            empty, self._empty_target = ("Пока нет ни одного трека с настроением. Разметьте несколько — волна подберёт "
                                         "остальные."), "labelfeed"
            self.empty_btn.setText("Открыть ленту разметки")
        self.empty.setVisible(bool(empty) and cur is None)
        self.empty_btn.setVisible(bool(empty) and cur is None)
        self.empty.setText(empty)
        self.cover.setVisible(cur is not None or not empty)
        self.cover.setFixedHeight(px + 8)
        if cur is None:
            self.cover.setPixmap(self.covers.pixmap(None, px))
            self.title.setText("" if empty else "Нажмите ▶ — волна подберёт треки")
            self.sub.setText("")
            self.badge.setText("")
        else:
            self.cover.setPixmap(self.covers.pixmap(cur["cover_key"], px))
            self.title.setText(cur["title"])
            feat = f" feat. {', '.join(cur['feat'])}" if cur.get("feat") else ""
            self.sub.setText(f"{cur['artist']}{feat} — {cur['album'] or 'без альбома'}")
            if cur.get("wave_kind") in ("guess", "auto"):
                conf = int((cur.get("wave_conf") or 0) * 100)
                who = "Определено автоматически" if cur["wave_kind"] == "auto" else "Модель думает"
                self.badge.setText(f"{who}: «{', '.join(cur.get('wave_moods', []))}» ({conf}%). Верно? 👍 / 👎")
                self.badge.setStyleSheet(f"color:{TOKENS['warning']}; font-size:13px;")
            else:
                self.badge.setText(f"Ваша метка: {', '.join(cur.get('wave_moods', [])) or '—'}")
                self.badge.setStyleSheet(f"color:{TOKENS['text_muted']}; font-size:13px;")
        self.chips.show_state([{"moods": sorted(self.selected)}])
        for b in self.chips.buttons.values():
            b.setEnabled(True)
        self._render_buttons()
