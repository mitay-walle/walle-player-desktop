"""Labelling feed: one track at a time, it plays, you press a mood key, the next one opens.

Keys (while this page is open): 1-9,0 toggle the n-th mood · Enter / → confirm and go on · S skip ·
A accept the model's suggestions · D reject them · Backspace undo the last step · Space pause/play.
Sources: tracks the model is least sure about (active learning), random unlabelled tracks, tracks that
have suggestions to review, or the list currently shown in the Library.
"""

from __future__ import annotations

import random

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QListWidget, QProgressBar, QPushButton,
                               QVBoxLayout,
                               QWidget)

from musicplayer.core import mood
from musicplayer.ui.context import AppContext
from musicplayer.ui.pages.library import EraChips, FlagChips, GenreChips, LangVocalChips, MoodChips
from musicplayer.ui.theme import TOKENS
from musicplayer.ui.widgets import CoverCache

SOURCES = [("Треки, в которых модель сомневается", "uncertain"), ("Случайные треки без меток", "random"),
           ("Проверить догадки модели", "review"), ("Треки из списка в библиотеке", "library")]
INTRO = '<h3>Лента разметки учит модель настроений</h3><p>Плеер по очереди включает треки, а вы отмечаете, какое у них настроение (и, если хотите, жанр, язык, голос). Каждая отметка — пример для модели: чем их больше, тем точнее она разметит остальную библиотеку сама.</p><p>Лучше всего брать <b>треки, в которых модель сомневается</b> — там ваша отметка полезнее всего. Модель заранее отмечает свои догадки: снимите неверные, добавьте недостающие и сохраните — снятая отметка тоже её учит.</p><p>Короткие ленты по 10–20 треков, а после — «Дообучить» на странице «Модель настроений».</p>'


class LabelFeedPage(QWidget):
    key = "labelfeed"

    def __init__(self, ctx: AppContext, covers: CoverCache, library_page=None, parent=None) -> None:
        super().__init__(parent)
        self.ctx, self.covers, self.library = ctx, covers, library_page
        self.queue: list[dict] = []
        self.seen: set[int] = set()               # tracks already shown this session: not offered again
        self.pos = -1
        self.chosen: set[str] = set()
        self.undo: list[dict] = []
        self.done = self.skipped = 0
        self.zoom = 1.0

        self.source = QComboBox()
        for label, _ in SOURCES:
            self.source.addItem(label)
        self.count = QComboBox()
        for n in (5, 10, 20):
            self.count.addItem(f"{n} треков", n)
        self.count.setCurrentIndex(1)
        self.preview = QCheckBox("С середины трека")
        self.preview.setToolTip("Включать каждый трек с трети длины: сразу слышно главное")
        self.preview.setChecked(True)
        self.btn_start = QPushButton("▶ Начать")
        self.btn_start.setObjectName("Primary")
        self.stats = QLabel()
        self.stats.setStyleSheet(f"color:{TOKENS['text_muted']}")
        self.btn_stop = QPushButton("■ Закончить ленту")
        self.btn_stop.setToolTip("Остановить ленту: уже сохранённые отметки остаются")
        top = QHBoxLayout()
        for w in (self.source, self.count, self.preview, self.btn_start, self.btn_stop):
            top.addWidget(w)
        top.addStretch(1)
        top.addWidget(self.stats)

        self.cover = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel("Выберите источник и нажмите «Начать ленту»")
        self.title.setStyleSheet("font-size:18px; font-weight:600;")
        self.title.setWordWrap(True)
        self.sub = QLabel()
        self.sub.setStyleSheet(f"color:{TOKENS['text_muted']}")
        self.sub.setWordWrap(True)
        self.sugg = QLabel()
        self.sugg.setWordWrap(True)
        self.sugg.setStyleSheet(f"color:{TOKENS['warning']}; font-size:13px;")
        self.chips = MoodChips()
        self.flag_chips = FlagChips()
        self.flag_chips.toggled.connect(self._flag_toggled)
        self.hint = QLabel("Клавиши: 1–0 настроение · Enter сохранить и дальше · S пропустить · A модель права · "
                           "D модель ошиблась · Backspace назад · Space пауза")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color:{TOKENS['text_dim']}")
        self.btn_ok, self.btn_skip = QPushButton("✓ Сохранить и дальше  (Enter)"), QPushButton("Пропустить  (S)")
        self.btn_ok.setObjectName("Primary")
        self.btn_acc, self.btn_rej = QPushButton("Модель права  (A)"), QPushButton("Модель ошиблась  (D)")
        self.btn_acc.setToolTip("Догадки модели становятся вашими метками")
        self.btn_rej.setToolTip("Модель больше не будет предлагать эти настроения для трека (это тоже учит её)")
        self.btn_undo = QPushButton("↶ Отменить (Backspace)")
        self.btn_pause = QPushButton("⏯ Пауза (Space)")
        self.btn_undo.setText("↶ Назад  (Backspace)")
        from musicplayer.ui.pages.library import TRASH_CSS
        self.btn_trash = QPushButton("🗑 Удалить файл…  (Del)")
        self.btn_trash.setStyleSheet(TRASH_CSS)
        self.btn_trash.setToolTip("Плохая запись? Файл уйдёт в корзину Windows (с подтверждением), лента пойдёт дальше")
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setFixedHeight(18)
        self.progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.intro = QLabel(INTRO)
        self.intro.setWordWrap(True)
        self.intro.setTextFormat(Qt.TextFormat.RichText)
        self.intro.setStyleSheet(f"background:{TOKENS['surface']}; border:1px solid {TOKENS['border']}; padding:12px;"
                                 "font-size:12px;")
        self.cover.setFixedSize(96, 96)
        # 1. the track
        head = QHBoxLayout()
        head.addWidget(self.cover, 0, Qt.AlignmentFlag.AlignTop)
        info = QVBoxLayout()
        info.addWidget(self.title)
        info.addWidget(self.sub)
        info.addWidget(self.sugg)
        info.addStretch(1)
        head.addLayout(info, 1)
        # 2. what you mark (a fixed map: every button always in the same place)
        self.chips.fixed_columns = 6
        self.lv_chips = LangVocalChips()
        self.lv_chips.lang_chosen.connect(lambda v: self._lv("lang", v))
        self.lv_chips.vocal_chosen.connect(lambda v: self._lv("vocal", v))
        self.genre_chips = GenreChips()
        self.genre_chips.toggled.connect(self._genre_toggled)
        self.era_chips = EraChips()
        self.era_chips.chosen.connect(self._era_chosen)
        marks = QWidget()
        marks.setMaximumWidth(1000)
        mv = QVBoxLayout(marks)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(6)
        for title, w in (("Настроение", self.chips), ("Тип записи и голос", self.flag_chips), (None, self.lv_chips),
                         ("Жанр", self.genre_chips), (None, self.era_chips)):
            if title:
                h = QLabel(title)
                h.setStyleSheet(f"color:{TOKENS['text_muted']}; font-weight:600; padding-top:4px;")
                mv.addWidget(h)
            mv.addWidget(w)
        # 3. done with this track
        actions = QHBoxLayout()
        for b in (self.btn_ok, self.btn_skip, self.btn_undo):
            actions.addWidget(b)
        actions.addStretch(1)
        actions.addWidget(self.btn_trash)
        card = QVBoxLayout()
        card.addWidget(self.progress)
        card.addLayout(head)
        card.addWidget(marks)
        card.addLayout(actions)
        card.addStretch(1)
        self.hint.hide()
        self.btn_pause.hide()                       # Space / the player bar pause the track
        self.btn_acc.hide()
        self.btn_rej.hide()
        self.upcoming = QListWidget()
        right = QVBoxLayout()
        right.addWidget(self.intro)
        right.addWidget(QLabel("<b>Дальше в ленте</b>"))
        right.addWidget(self.upcoming, 1)
        rw = QWidget()
        rw.setLayout(right)
        rw.setMaximumWidth(380)
        body = QHBoxLayout()
        body.addLayout(card, 3)
        body.addWidget(rw, 1)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(body, 1)

        self.btn_start.clicked.connect(lambda: self.start())
        self.btn_stop.clicked.connect(self.stop)
        self.btn_ok.clicked.connect(self.confirm)
        self.btn_skip.clicked.connect(self.skip)
        self.btn_acc.clicked.connect(lambda: self.resolve(True))
        self.btn_rej.clicked.connect(lambda: self.resolve(False))
        self.btn_undo.clicked.connect(self.undo_last)
        self.btn_trash.clicked.connect(self.trash_current)
        self.btn_pause.clicked.connect(ctx.player.toggle)
        self.chips.toggled.connect(self._chip_toggled)
        ctx.events.library_changed.connect(self._moods_changed)
        self._keys()
        self._render()

    # ---- keys ---------------------------------------------------------
    def _keys(self) -> None:
        def add(seq: str, fn) -> None:
            QShortcut(QKeySequence(seq), self, activated=fn).setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        for i, k in enumerate("1234567890"):
            add(k, lambda i=i: self.toggle_index(i))
        for seq in ("Return", "Enter", "Right"):
            add(seq, self.confirm)
        add("S", self.skip)
        add("A", lambda: self.resolve(True))
        add("D", lambda: self.resolve(False))
        add("Backspace", self.undo_last)
        add("Delete", self.trash_current)
        add("Space", self.ctx.player.toggle)

    # ---- session ------------------------------------------------------
    @property
    def current(self) -> dict | None:
        return self.queue[self.pos] if 0 <= self.pos < len(self.queue) else None

    def build(self, source: str, n: int) -> list[dict]:
        """A fresh random pick every time: from a wide pool of candidates, without tracks already shown this session."""
        db = self.ctx.db
        seen = self.seen
        if source == "uncertain":
            pool = [t for t in mood.uncertain_tracks(db, self.ctx.models, max(n * 10, 150)) if t["id"] not in seen]
            rows = random.sample(pool, min(n, len(pool)))
        elif source == "review":
            ids = [r[0] for r in db.conn.execute(
                "SELECT DISTINCT tm.track_id FROM track_moods tm JOIN tracks t ON t.id=tm.track_id "
                "WHERE tm.source NOT IN ('manual','seed') AND t.missing=0 AND NOT EXISTS(SELECT 1 FROM track_moods x "
                "WHERE x.track_id=tm.track_id AND x.source IN ('manual','seed'))")]
            ids = [i for i in ids if i not in seen]
            rows = [db.get_track(i) for i in random.sample(ids, min(n, len(ids)))]
        elif source == "library" and self.library is not None:
            rows = [t for t in self.library.model.rows if not t["moods"]][:n]
        else:
            rows = [t for t in db.search(no_mood=True, order="random", limit=n * 3) if t["id"] not in seen][:n]
        rows = [r for r in rows if r]
        seen.update(r["id"] for r in rows)
        return rows

    def start(self, source: str | None = None, n: int | None = None) -> int:
        if hasattr(self, "_model_cache"):
            del self._model_cache
        source = source or SOURCES[self.source.currentIndex()][1]
        n = n or self.count.currentData()
        self.queue = self.build(source, n)
        if source == "uncertain" and not self.queue:
            self.queue = self.build("random", n)             # no analysed tracks yet: fall back to random
        self.pos, self.undo, self.done, self.skipped = -1, [], 0, 0
        if source in ("random", "uncertain"):
            random.shuffle(self.queue) if source == "random" else None
        self._advance()
        self.setFocus()
        return len(self.queue)

    def _advance(self) -> None:
        self.pos += 1
        t = self.current
        # the model's guesses start out ticked: you only correct them (untick what is wrong, tick what is missing)
        self.guesses = self._model_guesses(t) if t else {}
        self.chosen = set(self.guesses) | {m for m in t["moods"]} if t else set()
        if t is not None:
            self.ctx.player.play_tracks([t])
            if self.preview.isChecked():
                QTimer.singleShot(700, lambda tid=t["id"]: self._seek_preview(tid))
        else:
            self.ctx.player.pause()
        self._render()

    def _model_guesses(self, t: dict) -> dict[str, float]:
        """Every mood the model believes in for this track (fresh prediction, several at once), else its stored ones."""
        if not hasattr(self, "_model_cache"):
            self._model_cache = mood.load_model(self.ctx.models)
        out: dict[str, float] = {}
        self.probs = {}
        if self._model_cache is not None:
            try:
                out = mood.track_guesses(self.ctx.db, self._model_cache, t["id"])
                self.probs = mood.track_probs(self.ctx.db, self._model_cache, t["id"])
            except Exception:
                out = {}
        src = t.get("mood_sources", {})
        for m in t["moods"]:
            if src.get(m) == "auto":
                out.setdefault(m, (t.get("mood_conf") or {}).get(m) or 0.5)
        for sg in t["mood_suggestions"]:
            out.setdefault(sg["mood"], sg["confidence"] or 0.0)
        return out

    def _seek_preview(self, tid: int) -> None:
        t = self.current
        if t and t["id"] == tid and (t.get("duration") or 0) > 40:
            self.ctx.player.seek(t["duration"] * 0.35)

    def _record(self, tid: int, kind: str) -> None:
        """Remember the state before a step so Backspace can restore it."""
        db = self.ctx.db
        t = db.get_track(tid)
        self.undo.append({"id": tid, "kind": kind, "pos": self.pos, "moods": list(t["moods"]),
                          "sugg": [(s["mood"], s["confidence"], s["source"]) for s in t["mood_suggestions"]],
                          "rejects": set(db.mood_negatives().get(tid, set()))})

    def toggle_index(self, i: int) -> None:
        names = list(self.chips.buttons)
        if self.current and i < len(names):
            n = names[i]
            self.chosen.symmetric_difference_update({n})
            self._render()

    def _chip_toggled(self, name: str, wanted: bool) -> None:
        (self.chosen.add if wanted else self.chosen.discard)(name)
        self._render()

    def confirm(self) -> None:
        """Ticked moods become your labels; the model's guesses you unticked are remembered as its mistakes."""
        t = self.current
        if t is None:
            return
        guessed = set(getattr(self, "guesses", {}))
        wrong = sorted(guessed - self.chosen)
        if not self.chosen and not wrong:         # nothing ticked and nothing to correct: same as skip
            return self.skip()
        self._record(t["id"], "label")
        if wrong:
            self.ctx.db.reject_moods(t["id"], wrong)
        if self.chosen:
            self.ctx.db.set_moods([t["id"]], sorted(self.chosen), "add")
        self.done += 1
        self._after([t["id"]])

    def skip(self) -> None:
        t = self.current
        if t is None:
            return
        self._record(t["id"], "skip")
        self.skipped += 1
        self._after([])

    def resolve(self, accept: bool) -> None:
        t = self.current
        if t is None or not t["mood_suggestions"]:
            return
        self._record(t["id"], "accept" if accept else "reject")
        self.ctx.db.resolve_suggestions([t["id"]], accept=accept)
        self.done += 1
        self._after([t["id"]])

    def _after(self, ids: list[int]) -> None:
        if ids:
            self.ctx.events.tracks_changed.emit(ids)
        self._advance()

    def undo_last(self) -> None:
        if not self.undo:
            return
        u = self.undo.pop()
        db = self.ctx.db
        tid = u["id"]
        if u["kind"] == "skip":
            self.skipped = max(0, self.skipped - 1)
        else:
            self.done = max(0, self.done - 1)
            db.conn.execute("DELETE FROM track_moods WHERE track_id=?", (tid,))
            db.set_moods([tid], u["moods"], "set") if u["moods"] else None
            db.conn.execute("DELETE FROM mood_rejects WHERE track_id=?", (tid,))
            for m in u["rejects"]:
                mid = db.add_mood(m)
                db.conn.execute("INSERT OR REPLACE INTO mood_rejects VALUES(?,?,0)", (tid, mid))
            for m, conf, src in u["sugg"]:
                mid = db.add_mood(m)
                db.conn.execute("INSERT OR REPLACE INTO track_moods VALUES(?,?,?,?)", (tid, mid, src, conf))
            db.refresh_fts(tid)
            self.ctx.events.tracks_changed.emit([tid])
        self.pos = u["pos"]
        t = self.current
        if t is not None:
            self.queue[self.pos] = db.get_track(t["id"])
            self.chosen = set(self.queue[self.pos]["moods"]) | {s["mood"] for s in self.queue[self.pos]["mood_suggestions"]}
            self.ctx.player.play_tracks([self.queue[self.pos]])
        self._render()

    def _lv(self, field: str, value) -> None:
        t = self.current
        if t:
            (self.ctx.db.set_language if field == "lang" else self.ctx.db.set_vocal)([t["id"]], value, src="manual")
            self.ctx.events.tracks_changed.emit([t["id"]])

    def stop(self) -> None:
        """End the feed early: what was saved stays; the source choice comes back."""
        if self.current is not None:
            self.ctx.player.pause()
        self.pos = len(self.queue)
        self._render()

    def trash_current(self) -> None:
        """A bad recording: to the Recycle Bin (asks first), then the next track."""
        from musicplayer.ui.pages.library import confirm_trash
        t = self.current
        if t is not None and confirm_trash(self, self.ctx, [t]):
            self.skipped += 1
            self._advance()

    def _genre_toggled(self, genre: str, wanted: bool) -> None:
        t = self.current
        if t is not None:
            self.ctx.db.set_genres([t["id"]], [genre], "add" if wanted else "remove")
            self.queue[self.pos] = self.ctx.db.get_track(t["id"])
            self.ctx.events.tracks_changed.emit([t["id"]])

    def _era_chosen(self, decade) -> None:
        t = self.current
        if t is not None:
            self.ctx.db.set_era([t["id"]], decade)
            self.queue[self.pos] = self.ctx.db.get_track(t["id"])
            self.ctx.events.tracks_changed.emit([t["id"]])
            self._render()

    def _flag_toggled(self, flag: str, wanted: bool) -> None:
        t = self.current
        if t is not None:
            self.ctx.db.set_flags([t["id"]], [flag], "add" if wanted else "remove")
            self.queue[self.pos] = self.ctx.db.get_track(t["id"])
            self.ctx.events.tracks_changed.emit([t["id"]])
            self._render()

    def _moods_changed(self) -> None:
        self.chips.set_moods([m["name"] for m in self.ctx.db.list_moods()])
        self.chips.set_counts(self.ctx.db.mood_example_counts())
        self._render()

    # ---- rendering ----------------------------------------------------
    def apply_zoom(self, f: float) -> None:
        self.zoom = f
        self._render()

    def _render(self) -> None:
        names = [m["name"] for m in self.ctx.db.list_moods()]
        if self.chips.needs_update(names):
            self.chips.set_moods(names)
        self.chips.set_counts(self.ctx.db.mood_example_counts())
        t = self.current
        px = int(96 * self.zoom)
        self.cover.setFixedSize(px, px)
        for b in (self.btn_ok, self.btn_skip, self.btn_undo, self.btn_pause):
            b.setEnabled(t is not None or b is self.btn_undo and bool(self.undo))
        self.btn_undo.setEnabled(bool(self.undo))
        self.btn_acc.setEnabled(bool(t and t["mood_suggestions"]))
        self.btn_rej.setEnabled(bool(t and t["mood_suggestions"]))
        left = max(0, len(self.queue) - self.pos - 1) if t else 0
        self.stats.setText(f"Трек {self.pos + 1} из {len(self.queue)} · размечено {self.done} · пропущено {self.skipped}"
                           if t else (f"Размечено {self.done} · пропущено {self.skipped}" if self.queue else ""))
        working = t is not None
        for w in (self.source, self.count, self.preview, self.btn_start):   # while it runs: no "start" again
            w.setVisible(not working)
        self.btn_stop.setVisible(working)
        self.btn_start.setText("▶ Ещё лента" if self.queue and not working else "▶ Начать")
        self.intro.setVisible(True)               # the explanation stays on screen while labelling
        self.progress.setVisible(bool(self.queue))
        self.progress.setRange(0, max(len(self.queue), 1))
        self.progress.setValue(min(self.pos + 1, len(self.queue)) if t else len(self.queue))
        self.progress.setFormat(f"Трек {self.pos + 1} из {len(self.queue)}" if t else
                                (f"Готово: все {len(self.queue)} треков" if self.queue else ""))
        self.btn_trash.setVisible(working)
        for w in (self.chips, self.flag_chips, self.lv_chips, self.genre_chips, self.era_chips, self.btn_ok, self.btn_skip,
                  self.cover, self.sub):
            w.setVisible(working)
        self.btn_acc.setVisible(False)            # one way to answer: correct the ticks and save
        self.btn_rej.setVisible(False)
        self.btn_undo.setVisible(working or bool(self.undo))
        if t is None:
            self.cover.setPixmap(self.covers.pixmap(None, px))
            finished = bool(self.queue)
            self.title.setText("Лента закончилась — спасибо! Теперь «Дообучить» на странице «Модель настроений»."
                               if finished else "")
            self.sub.setText(mood.next_step(self.ctx.db, self.ctx.models)["text"] if finished else "")
            self.sugg.setText("")
            self.chips.set_probs({})
            self.chips.show_state([])
            self.flag_chips.show_state([])
            self.lv_chips.show_state([])
            self.genre_chips.show_state([])
            self.era_chips.show_state([])
            self.upcoming.clear()
            return
        self.cover.setPixmap(self.covers.pixmap(t["cover_key"], px))
        feat = f" feat. {', '.join(t['feat'])}" if t.get("feat") else ""
        self.title.setText(t["title"])
        self.sub.setText(f"{t['artist']}{feat} — {t['album'] or 'без альбома'} · {t['year'] or '—'}"
                         + (f"\nЖанры: {', '.join(t['genres'])}" if t["genres"] else ""))
        src = t.get("mood_sources", {})
        sg = [{"mood": m, "confidence": (t.get("mood_conf") or {}).get(m)} for m in t["moods"] if src.get(m) == "auto"]             + list(t["mood_suggestions"])
        self.sugg.setText("Модель отметила: " + ", ".join(f"{s['mood']} ({int((s['confidence'] or 0) * 100)}%)" for s in sg)
                          + ". Снимите лишнее, добавьте нужное и сохраните. Заливка кнопки — насколько модель уверена." if sg
                          else "Отметьте подходящие настроения и сохраните.")
        fake = dict(t)
        fake["moods"] = sorted(self.chosen)
        self.chips.set_probs(getattr(self, "probs", {}))
        self.chips.show_state([fake])
        self.flag_chips.show_state([t])
        self.lv_chips.show_state([t])
        self.genre_chips.show_state([t])
        self.era_chips.show_state([t])
        self.upcoming.clear()
        for u in self.queue[self.pos + 1:self.pos + 9]:
            self.upcoming.addItem(f"{u['title']} — {u['artist']}")
