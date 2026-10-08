"""Library page: fast search, filters, the track table and the side panel for genres and moods.

The side panel is the main place to label moods: select tracks, click mood chips (several can be
active); model/Claude suggestions show up there with accept/reject buttons."""

from __future__ import annotations

from PySide6.QtCore import QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox,
                               QSplitter, QVBoxLayout, QWidget)

from musicplayer.core import moods as moods_vocab
from musicplayer.core.similar import similar_tracks
from musicplayer.ui.context import AppContext
from musicplayer.ui.pages.playlists import playlist_menu
from musicplayer.ui.theme import TOKENS
from musicplayer.ui.widgets import CoverCache, FlowLayout, TrackModel, TrackView, fmt_date

SORTS = [("Исполнитель", "artist", False), ("Название", "title", False), ("Альбом", "album", False),
         ("Год", "year", False), ("Больше прослушиваний", "play_count", True),
         ("Меньше прослушиваний", "play_count", False), ("Давно не слушал", "last_played", False),
         ("Недавно слушал", "last_played", True), ("Недавно добавлено", "added", True),
         ("Случайно", "random", False), ("Случайно, чаще редкие", "rare_random", False)]
PERIODS = [("за всё время", None), ("за неделю", 7), ("за месяц", 30), ("за год", 365)]
LIMIT = 5000


PAD = "border-radius:15px; padding:4px 13px; font-size:12px; min-height:20px;"
PAD_BIG = "border-radius:17px; padding:7px 14px; font-size:13px; min-height:22px;"


def confirm_trash(parent, ctx, tracks: list[dict]) -> bool:
    """Ask, then move the files to the Recycle Bin and drop them from the catalogue (the user can restore them from the bin)."""
    from PySide6.QtWidgets import QMessageBox

    from musicplayer.core.trash import trash_tracks
    if not tracks:
        return False
    names = "\n".join(f"• {t['artist']} — {t['title']}" for t in tracks[:8]) + (f"\n… и ещё {len(tracks) - 8}" if len(tracks) > 8 else "")
    if QMessageBox.question(parent, "Удалить файлы?", f"Переместить в корзину Windows ({len(tracks)}):\n{names}\n\nИх можно восстановить из корзины.",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                            QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
        return False
    cur = ctx.player.current
    if cur and any(cur["id"] == t["id"] for t in tracks):
        ctx.player.next()                         # the file must not be held open by the player
    res = trash_tracks(ctx.db, tracks)
    ctx.events.library_changed.emit()
    if res["failed"]:
        QMessageBox.warning(parent, "Не всё удалено", "Не удалось удалить:\n" + "\n".join(res["failed"][:5]))
    return bool(res["deleted"])


TRASH_CSS = ("QPushButton { color:#E88A8A; background:transparent; border:1px solid rgba(232,138,138,70); "
             "padding:2px 10px; font-size:12px; } QPushButton:hover { background:rgba(232,138,138,40); }")
SMALL_CHIP = ("QPushButton { padding:4px 12px; font-size:12px; min-height:20px; border-radius:15px; background:#18181b;"
              " border:1px solid #2c2c31; } QPushButton:hover { background:#222226; }"
              " QPushButton:checked { background:#D5A52C; color:#111113; border:1px solid #D5A52C; }")
PARTIAL_CHIP = SMALL_CHIP + f" QPushButton {{ border:1px dashed #D5A52C; }}"


def set_css(w, css: str) -> None:
    """setStyleSheet only when it changes: re-styling is slow and chips are refreshed on every selection."""
    if w.property("_css") != css:
        w.setProperty("_css", css)
        w.setStyleSheet(css)


class MoodChips(QWidget):
    _icons: dict = {}
    """Checkable mood buttons grouped by section (basic / rock-punk-metal / rave / ...).

    State reflects the selected tracks (checked = all have it, dashed = some).  The first ten moods of the
    vocabulary get the keys 1-9,0 where hotkeys exist."""

    toggled = Signal(str, bool)   # mood, wanted state

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(1)
        self.count = QLabel()                       # how many moods are set; green from 3 on
        self.box.addWidget(self.count)
        self.buttons: dict[str, QPushButton] = {}
        self._parts: list[QWidget] = []
        self._flows: list[FlowLayout] = []
        self.big = False                           # tight window: two roomy columns instead of small wrapped chips
        self.fixed_columns = 0                     # >0: a fixed grid (every mood always in the same place)

    def needs_update(self, names: list[str]) -> bool:
        return list(self.buttons) != moods_vocab.ordered(names)

    def set_moods(self, names: list[str]) -> None:
        for w in self._parts:
            w.deleteLater()
        self._parts, self.buttons, self._flows = [], {}, []
        holder = QWidget()
        flow = FlowLayout(holder, 3)
        flow.set_columns(self.fixed_columns or (2 if self.big else 0))
        flow.header = False
        self._flows.append(flow)
        for n in moods_vocab.ordered(names):
            col, glyph = moods_vocab.style_of(n)
            b = QPushButton(n)
            b.setProperty("moodname", n)
            b.setIconSize(QSize(18, 18))
            b.setToolTip(moods_vocab.group_of(n))
            b.setProperty("moodcol", col)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self._style(b, False, False)
            b.clicked.connect(lambda checked, n=n, b=b: (self._style(b, checked, False), (self._update_count(), self.toggled.emit(n, checked))))
            flow.addWidget(b)
            self.buttons[n] = b
        self.box.addWidget(holder)
        self._parts.append(holder)
        self._update_count()
        self.updateGeometry()

    FEW = 10                                         # fewer training examples than this: the model is weak on the mood

    def set_counts(self, counts: dict[str, int]) -> None:
        """Mark moods the model has few examples of: «· N» after the name, a dim tooltip with the advice."""
        for n, b in self.buttons.items():
            k = counts.get(n, 0)

            few = k < self.FEW
            b.setText(f"{n} · {k}" if few else n)
            b.setToolTip(f"Мало примеров для модели: {k} (нужно хотя бы {self.FEW}). Размечайте этим настроением подходящие треки."
                         if few else f"Примеров для модели: {k}")

    def _update_count(self) -> None:
        n = sum(b.isChecked() for b in self.buttons.values())
        ok = n >= 3
        word = "метка" if n % 10 == 1 and n != 11 else "метки" if 2 <= n % 10 <= 4 and not 12 <= n <= 14 else "меток"
        self.count.setText(f"✔ {n} {word}" if ok else (f"Выбрано: {n}" if n else "Метки не выбраны"))
        self.count.setStyleSheet(f"font-size:12px; padding:0 4px; color:{'#7BD88F' if ok else TOKENS['text_dim']}; "
                                 f"font-weight:{600 if ok else 400};")

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        big = self.width() < 520 and not self.fixed_columns
        if self.fixed_columns:
            for f in self._flows:
                f.set_columns(self.fixed_columns)
        if big != self.big:
            self.big = big
            for f in self._flows:
                f.set_columns(2 if big else 0)
            for b in self.buttons.values():
                set_css(b, self._css(b.property("moodcol"), b.isChecked(), bool(b.property("partial")), big))

    def show_state(self, tracks: list[dict]) -> None:
        for n, b in self.buttons.items():
            have = sum(n in t["moods"] for t in tracks)
            b.blockSignals(True)
            b.setChecked(bool(tracks) and have == len(tracks))
            b.setProperty("partial", 0 < have < len(tracks))
            self._style(b, b.isChecked(), 0 < have < len(tracks))
            b.setEnabled(bool(tracks))
            b.blockSignals(False)
        self._update_count()

    @staticmethod
    def _css(col: str, checked: bool, partial: bool, big: bool = False) -> str:
        """One calm style for every mood (design kit): neutral when off, Ochre when on, dashed Ochre when partly set."""
        pad = PAD_BIG if big else PAD
        if checked:
            return f"QPushButton {{ background:#D5A52C; color:#111113; border:1px solid #D5A52C; {pad} }}"
        edge = "border:1px dashed #D5A52C;" if partial else "border:1px solid #2c2c31;"
        return (f"QPushButton {{ background:#18181b; color:#E6E3DA; {pad} {edge} }}"
                f"QPushButton:hover {{ background:#222226; }}"
                f"QPushButton:disabled {{ background:#141416; color:#5F5F63; border:1px solid #222226; {pad} }}")

    @staticmethod
    def _icon(name: str, checked: bool):
        """The mood's glyph as a proper icon (16 px glyph instead of a tiny text character)."""
        from PySide6.QtGui import QFont, QIcon, QPainter, QPixmap
        key = (name, checked)
        cache = MoodChips._icons
        if key not in cache:
            glyph = moods_vocab.style_of(name)[1]
            pm = QPixmap(20, 20)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            f = QFont("Segoe UI Symbol", 13)
            p.setFont(f)
            p.setPen(QColor("#111113" if checked else moods_vocab.style_of(name)[0]))
            p.drawText(pm.rect(), int(Qt.AlignmentFlag.AlignCenter), glyph)
            p.end()
            cache[key] = QIcon(pm)
        return cache[key]

    def set_probs(self, probs: dict[str, float] | None) -> None:
        """Show how sure the model is: each button is filled with Ochre in proportion to its probability."""
        self.probs = probs or {}
        for n, b in self.buttons.items():
            p = self.probs.get(n)
            b.setToolTip(f"Модель уверена на {p * 100:.0f}%" if p is not None else moods_vocab.group_of(n))
            self._style(b, b.isChecked(), bool(b.property("partial")))

    def _style(self, b, checked: bool, partial: bool) -> None:
        p = getattr(self, "probs", {}).get(b.property("moodname"))
        css = self._css(b.property("moodcol"), checked, partial, self.big)
        if p is not None and not checked:              # a probability bar inside the button
            stop = max(0.001, min(0.999, p))
            css = css.replace("background:#18181b;",
                              f"background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 rgba(213,165,44,115), "
                              f"stop:{stop:.3f} rgba(213,165,44,115), stop:{stop + 0.001:.3f} #18181b, stop:1 #18181b);", 1)
        set_css(b, css)
        if b.property("_iconstate") != checked:
            b.setProperty("_iconstate", checked)
            b.setIcon(self._icon(b.property("moodname"), checked))


class GenreChips(QWidget):
    """Quick-pick genres (emocore, djent, pop punk, post-hardcore, power metal ...) of the selected tracks."""

    toggled = Signal(str, bool)

    FAMILY_COLOURS = {"Rock": "#E8A87C", "Punk": "#F28B82", "Metal": "#AEB8C6", "Electronic": "#7FD6E0",
                      "Hip-hop": "#C5A8F5", "Pop": "#F5A9C4", "Jazz": "#C9A27A", "Soundtrack": "#D9BE95", "Oriental": "#E0C27A"}

    @staticmethod
    def _css(col: str, family: bool) -> str:
        r, g, b = int(col[1:3], 16), int(col[3:5], 16), int(col[5:7], 16)
        if family:
            return (f"QPushButton {{ padding:4px 13px; font-size:12px; min-height:20px; font-weight:600; border-radius:15px; "
                    f"border:1px solid rgba({r},{g},{b},90); background:rgba({r},{g},{b},40); color:#E6E3DA; }}"
                    f"QPushButton:checked {{ background:{col}; color:#14171C; }}")
        return (f"QPushButton {{ padding:4px 12px; font-size:12px; min-height:20px; border-radius:15px; "
                f"border:1px solid rgba({r},{g},{b},60); background:#18181b; color:{col}; }}"
                f"QPushButton:checked {{ background:rgba({r},{g},{b},170); color:#14171C; }}")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        from musicplayer.core.genres import GENRE_TREE, PARENT
        self.tree, self.parent_of = GENRE_TREE, PARENT
        flow = FlowLayout(self, 3)                   # each family is followed by its own sub-genres, in its colour
        flow.header = False
        self.buttons: dict[str, QPushButton] = {}
        for fam, subs in GENRE_TREE.items():
            col = self.FAMILY_COLOURS.get(fam, "#B8C4CF")
            for g in [fam, *subs]:
                b = QPushButton(g if g == fam else f"› {g}")
                b.setCheckable(True)
                b.setStyleSheet(self._css(col, g == fam))
                b.clicked.connect(lambda checked, g=g: self._clicked(g, checked))
                flow.addWidget(b)
                self.buttons[g] = b
        self._sync_visibility()

    def _clicked(self, g: str, on: bool) -> None:
        """A sub-genre sets its family too; switching a family off clears its sub-genres."""
        fam = self.parent_of.get(g)
        self.toggled.emit(g, on)
        if on and fam and not self.buttons[fam].isChecked():
            self.buttons[fam].setChecked(True)
            self.toggled.emit(fam, True)
        if not on and g in self.tree:
            for s in self.tree[g]:
                if self.buttons[s].isChecked():
                    self.buttons[s].setChecked(False)
                    self.toggled.emit(s, False)
        self._sync_visibility()

    def _sync_visibility(self) -> None:
        for s, fam in self.parent_of.items():
            self.buttons[s].setVisible(self.buttons[fam].isChecked() or self.buttons[s].isChecked())

    def show_state(self, tracks: list[dict]) -> None:
        for g, b in self.buttons.items():
            have = sum(g.lower() in [x.lower() for x in t.get("genres", [])] for t in tracks)
            b.blockSignals(True)
            b.setChecked(bool(tracks) and have == len(tracks))
            b.setEnabled(bool(tracks))
            b.blockSignals(False)
        self._sync_visibility()


class EraChips(QWidget):
    """Decade of the recording (one value; a click on the lit one clears it)."""

    chosen = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        from musicplayer.core.genres import ERAS, era_label
        flow = FlowLayout(self, 3)
        flow.header = False
        head = QLabel("Эпоха:")
        head.setStyleSheet(f"color:{TOKENS['text_dim']}; font-size:11px; padding:0 4px;")
        flow.addWidget(head)
        self.buttons: dict[int, QPushButton] = {}
        for d in ERAS:
            b = QPushButton(era_label(d))
            b.setCheckable(True)
            b.setStyleSheet(SMALL_CHIP)
            b.clicked.connect(lambda checked, d=d: self.chosen.emit(d if checked else None))
            flow.addWidget(b)
            self.buttons[d] = b

    def show_state(self, tracks: list[dict]) -> None:
        vals = {(t.get("year") // 10 * 10) if t.get("year") else t.get("era") for t in tracks}
        for d, b in self.buttons.items():
            b.blockSignals(True)
            b.setChecked(bool(tracks) and vals == {d})
            b.setEnabled(bool(tracks) and not any(t.get("year") for t in tracks))
            b.setToolTip("Год записи известен из файла" if any(t.get("year") for t in tracks) else "")
            b.blockSignals(False)


class LangVocalChips(QWidget):
    """Language of the lyrics and instrumental / vocals of the selected tracks (one value each; click again to clear)."""

    lang_chosen = Signal(object)     # code or None
    vocal_chosen = Signal(object)    # 'inst' | 'voc' | None
    LANG = [("ru", "Русский"), ("en", "English"), ("ja", "日本語"), ("ko", "한국어"), ("fr", "Français"), ("other", "Другой")]
    VOCAL = [("voc", "С вокалом"), ("inst", "Инструментал")]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        holder = QVBoxLayout(self)
        holder.setContentsMargins(0, 0, 0, 0)
        holder.setSpacing(2)
        self.lang_btns: dict[str, QPushButton] = {}
        self.vocal_btns: dict[str, QPushButton] = {}
        for items, store, sig, title in ((self.LANG, self.lang_btns, self.lang_chosen, "Язык"),
                                         (self.VOCAL, self.vocal_btns, self.vocal_chosen, "Голос")):
            box = QWidget()
            flow = FlowLayout(box, 3)
            head = QLabel(title.upper())
            head.setStyleSheet(f"color:{TOKENS['text_muted']}; font-size:10px; letter-spacing:1px; padding:0 6px 0 2px;")
            flow.addWidget(head)
            for key, label in items:
                b = QPushButton(label)
                b.setCheckable(True)
                b.setStyleSheet(SMALL_CHIP)
                b.clicked.connect(lambda checked, k=key, sig=sig: sig.emit(k if checked else None))
                flow.addWidget(b)
                store[key] = b
            holder.addWidget(box)

    def show_state(self, tracks: list[dict]) -> None:
        for field, store in (("lang", self.lang_btns), ("vocal", self.vocal_btns)):
            vals = {t.get(field) for t in tracks}
            for k, b in store.items():
                b.blockSignals(True)
                b.setChecked(bool(tracks) and vals == {k})
                b.setEnabled(bool(tracks))
                b.blockSignals(False)


class FlagChips(QWidget):
    """Toggles for the recording type (live / acoustic / demo / remix / cover) of the selected tracks."""

    toggled = Signal(str, bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        from musicplayer.core.flags import FLAGS
        flow = FlowLayout(self, 3)
        flow.header = False
        self.buttons: dict[str, QPushButton] = {}
        for key, label in FLAGS.items():
            b = QPushButton(label)
            b.setCheckable(True)
            b.setStyleSheet(SMALL_CHIP)
            b.clicked.connect(lambda checked, k=key: self.toggled.emit(k, checked))
            flow.addWidget(b)
            self.buttons[key] = b

    def show_state(self, tracks: list[dict]) -> None:
        for k, b in self.buttons.items():
            have = sum(k in t.get("flags", []) for t in tracks)
            b.blockSignals(True)
            b.setChecked(bool(tracks) and have == len(tracks))
            set_css(b, PARTIAL_CHIP if 0 < have < len(tracks) else SMALL_CHIP)
            b.setEnabled(bool(tracks))
            b.blockSignals(False)


class LibraryPage(QWidget):
    key = "library"

    def __init__(self, ctx: AppContext, covers: CoverCache, parent=None) -> None:
        super().__init__(parent)
        self.ctx, self.covers = ctx, covers
        self.mode_title = ""
        self._selection: list[dict] = []

        self.search = QLineEdit(placeholderText="Поиск: название, исполнитель, альбом, жанр, настроение, год…")
        self.search.setClearButtonEnabled(True)
        self.genre = QComboBox()
        self.mood = QComboBox()
        self.rec_type = QComboBox()
        self.rec_type.addItem("Любая запись", None)
        self.rec_type.addItem("Без live и demo", "clean")
        for key, label in __import__("musicplayer.core.flags", fromlist=["FLAGS"]).FLAGS.items():
            self.rec_type.addItem(label, key)
        self.sort = QComboBox()
        for label, *_ in SORTS:
            self.sort.addItem(label)
        self.limit_plays = QCheckBox("Прослушано ≤")
        self.max_plays = QSpinBox(maximum=9999, value=2)
        self.plays_period = QComboBox()
        for label, _ in PERIODS:
            self.plays_period.addItem(label)
        self.stale = QCheckBox("Не слушал дней ≥")
        self.stale_days = QSpinBox(maximum=9999, value=90)
        self.count_label = QLabel()
        self.count_label.setStyleSheet(f"color:{TOKENS['text_muted']}")

        bar = QHBoxLayout()
        for w in (self.search, self.genre, self.mood, self.rec_type, self.sort):
            bar.addWidget(w, 3 if w is self.search else 1)
        bar2 = QHBoxLayout()
        for w in (self.limit_plays, self.max_plays, self.plays_period, self.stale, self.stale_days):
            bar2.addWidget(w)
        bar2.addStretch(1)
        bar2.addWidget(self.count_label)

        self.model = TrackModel(covers)
        self.view = TrackView(self.model)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addLayout(bar)
        lv.addLayout(bar2)
        lv.addWidget(self.view, 1)

        # side panel
        self.cover = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.cover.setFixedHeight(220)
        self.info = QLabel(wordWrap=True)
        self.genre_edit = QLineEdit(placeholderText="Жанры через запятую")
        self.genre_apply = QPushButton("Сохранить жанры в БД")
        self.chips = MoodChips()
        self.flag_chips = FlagChips()
        self.lv_chips = LangVocalChips()
        self.genre_chips = GenreChips()
        self.era_chips = EraChips()
        self.btn_edit = QPushButton("✎ Исправить название / исполнителя…  (F2)")
        self.btn_edit.setToolTip("Исправить название, исполнителя, альбом, год у выбранных треков (только в каталоге)")
        self.btn_fav = QPushButton("♡ В избранное")
        self.btn_fav.setToolTip("Добавить выбранные треки в избранное или убрать оттуда")
        self.btn_copy = QPushButton("⧉ Копировать метки")
        self.btn_copy.setToolTip("Скопировать все метки выбранного трека (настроения, тип записи, язык, вокал, жанры, эпоха)")
        self.btn_paste = QPushButton("⎘ Вставить метки")
        self.btn_trash = QPushButton("🗑 Удалить файл…  (Del)")
        self.btn_trash.setStyleSheet(TRASH_CSS)
        self.btn_trash.setToolTip("Переместить файлы выбранных треков в корзину Windows (с подтверждением)")
        self.sugg_box = QVBoxLayout()
        self.sugg_title = QLabel("Предложения")
        self.sugg_title.setStyleSheet(f"color:{TOKENS['warning']}")
        self.btn_play = QPushButton("▶ Играть")
        self.btn_queue = QPushButton("В очередь")
        self.btn_next = QPushButton("Следующим")
        self.btn_similar = QPushButton("Похожие")
        self.btn_all = QPushButton("Показать всё")
        self.btn_pl = QPushButton("В плейлист…")
        side = QWidget()
        side.setMinimumWidth(300)
        self._zoom = 1.0
        sv = QVBoxLayout(side)
        sv.addWidget(self.cover)
        sv.addWidget(self.info)
        sv.addWidget(QLabel("Жанры"))
        sv.addWidget(self.genre_edit)
        sv.addWidget(self.genre_apply)
        sv.addWidget(QLabel("Настроения (клик — добавить/убрать у выбранных)"))
        sv.addWidget(self.chips)
        sv.addWidget(QLabel("Тип записи (live, acoustic, demo…)"))
        sv.addWidget(self.flag_chips)
        sv.addWidget(self.lv_chips)
        sv.addWidget(self.genre_chips)
        sv.addWidget(self.era_chips)
        sv.addWidget(self.btn_fav)
        sv.addWidget(self.btn_edit)
        clip_row = QHBoxLayout()
        clip_row.addWidget(self.btn_copy)
        clip_row.addWidget(self.btn_paste)
        sv.addLayout(clip_row)
        sv.addWidget(self.sugg_title)
        sv.addLayout(self.sugg_box)
        row = QHBoxLayout()
        row.addWidget(self.btn_play)
        row.addWidget(self.btn_queue)
        sv.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(self.btn_next)
        row2.addWidget(self.btn_similar)
        sv.addLayout(row2)
        sv.addWidget(self.btn_pl)
        sv.addWidget(self.btn_all)
        sv.addStretch(1)
        sv.addSpacing(16)                              # dangerous action: apart from the rest, small, at the bottom left
        sv.addWidget(self.btn_trash, 0, Qt.AlignmentFlag.AlignLeft)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(side)
        split.setStretchFactor(0, 1)
        split.setSizes([1000, 330])
        lay = QVBoxLayout(self)
        lay.addWidget(split)

        self._timer = QTimer(self, singleShot=True, interval=150)
        self._timer.timeout.connect(self.refresh)
        for w in (self.search,):
            w.textChanged.connect(lambda *_: self._timer.start())
        for w in (self.genre, self.mood, self.rec_type, self.sort, self.plays_period):
            w.currentIndexChanged.connect(lambda *_: self._timer.start())
        for w in (self.limit_plays, self.stale):
            w.toggled.connect(lambda *_: self._timer.start())
        for w in (self.max_plays, self.stale_days):
            w.valueChanged.connect(lambda *_: self._timer.start())

        self.view.play_requested.connect(self.play_row)
        self.view.ids_selected.connect(self._on_selection)
        self.chips.toggled.connect(self._toggle_mood)
        self.flag_chips.toggled.connect(self._toggle_flag)
        self.genre_chips.toggled.connect(self._toggle_genre)
        self.era_chips.chosen.connect(self._set_era)
        self.btn_trash.clicked.connect(self.trash_selected)
        self.btn_copy.clicked.connect(self.copy_labels)
        self.btn_edit.clicked.connect(self.edit_selected)
        self.btn_fav.clicked.connect(self.toggle_favorite)
        ctx.events.favorites_changed.connect(lambda: self._on_selection(self.view.selected_ids()))
        self.btn_paste.clicked.connect(self.paste_labels)
        self.lv_chips.lang_chosen.connect(lambda v: self._set_lv("lang", v))
        self.lv_chips.vocal_chosen.connect(lambda v: self._set_lv("vocal", v))
        self.genre_apply.clicked.connect(self._apply_genres)
        self.btn_play.clicked.connect(self.play_selected)
        self.btn_queue.clicked.connect(lambda: self.ctx.player.enqueue(self._selection))
        self.btn_next.clicked.connect(lambda: self.ctx.player.enqueue(self._selection, next_=True))
        self.btn_similar.clicked.connect(self.show_similar)
        self.btn_all.clicked.connect(self.reset_filters)
        self.btn_pl.clicked.connect(lambda: playlist_menu(self, self.ctx, [t["id"] for t in self._selection]))
        ctx.events.library_changed.connect(self.reload_lists)
        ctx.events.tracks_changed.connect(self._tracks_changed)
        ctx.events.show_track.connect(self.show_track)
        ctx.events.show_rows.connect(self.set_rows)
        self._hotkeys()
        ctx.player.play_counted.connect(lambda tid: self._tracks_changed([tid]))
        self.reload_lists()

    def _hotkeys(self) -> None:
        """With the table focused: 1-9,0 toggle the n-th mood on the selection, A accepts / D rejects model
        suggestions of the selection and moves to the next row, Enter plays."""
        def add(seq: str, fn) -> None:
            QShortcut(QKeySequence(seq), self.view, activated=fn).setContext(
                Qt.ShortcutContext.WidgetWithChildrenShortcut)
        for i, key in enumerate("1234567890"):
            add(key, lambda i=i: self.toggle_mood_index(i))
        add("F2", self.edit_selected)
        add("Delete", self.trash_selected)          # files of the selected tracks -> Recycle Bin (asks first)
        add("A", lambda: self.quick_resolve(True))
        add("D", lambda: self.quick_resolve(False))
        add("Return", self.play_selected)

    def toggle_mood_index(self, i: int) -> None:
        names = list(self.chips.buttons)
        if i < len(names) and self._selection:
            b = self.chips.buttons[names[i]]
            self._toggle_mood(names[i], not b.isChecked())

    def quick_resolve(self, accept: bool) -> None:
        """Accept/reject every suggestion of the selected tracks, then select the next row (fast review)."""
        ids = [t["id"] for t in self._selection]
        if not ids:
            return
        rows = self.model.rows
        last = max((i for i, t in enumerate(rows) if t["id"] in ids), default=-1)
        self.ctx.db.resolve_suggestions(ids, accept=accept)
        self.ctx.events.tracks_changed.emit(ids)
        if last + 1 < len(rows):
            self.view.selectRow(last + 1)

    def apply_zoom(self, f: float) -> None:
        self.view.apply_zoom(f)
        self.cover.setFixedHeight(int(220 * f))
        self._zoom = f
        self._on_selection(self.view.selected_ids())

    # ---- data ---------------------------------------------------------
    def reload_lists(self) -> None:
        db = self.ctx.db
        for combo, items, first in ((self.genre, [g["name"] for g in db.list_values("genre")], "Все жанры"),
                                    (self.mood, [m["name"] for m in db.list_moods()],
                                     "Все настроения")):
            keep = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(first)
            if combo is self.mood:
                combo.addItem("— без настроения —")
                combo.addItem("— есть предложения —")
            combo.addItems(items)
            i = combo.findText(keep)
            combo.setCurrentIndex(max(i, 0))
            combo.blockSignals(False)
        self.chips.set_moods([m["name"] for m in db.list_moods()])
        self.chips.set_counts(db.mood_example_counts())
        self.refresh()

    def filters(self) -> dict:
        _, order, desc = SORTS[self.sort.currentIndex()]
        kw: dict = {"text": self.search.text(), "order": order, "desc": desc, "limit": LIMIT}
        if self.genre.currentIndex() > 0:
            kw["genre"] = self.genre.currentText()
        mi = self.mood.currentIndex()
        if mi == 1:
            kw["no_mood"] = True
        elif mi == 2:
            kw["mood_status"] = "suggested"
            kw["mood"] = None
        elif mi > 2:
            kw["mood"] = self.mood.currentText()
        if self.limit_plays.isChecked():
            kw["max_plays"] = self.max_plays.value()
            days = PERIODS[self.plays_period.currentIndex()][1]
            if days:
                kw["plays_days"] = days
        if self.stale.isChecked():
            kw["not_played_days"] = self.stale_days.value()
        rec = self.rec_type.currentData()
        if rec == "clean":
            kw["exclude_flags"] = ["live", "demo"]
        elif rec:
            kw["flag"] = rec
        return kw

    def refresh(self) -> None:
        if self.ctx.closed:          # a debounce timer can fire after the window was closed
            return
        kw = self.filters()
        suggested_only = kw.get("mood_status") == "suggested" and kw.get("mood") is None
        if suggested_only:
            kw.pop("mood_status")
            kw.pop("mood")
        rows = self.ctx.db.search(**kw)
        if suggested_only:
            rows = [r for r in rows if r["mood_suggestions"]]
        keep = self.view.selected_ids()
        self.model.set_rows(rows)
        self.view.select_ids(keep)
        total = self.ctx.db.conn.execute("SELECT COUNT(*) FROM tracks WHERE missing=0").fetchone()[0]
        self.count_label.setText(f"{self.mode_title or 'Найдено'}: {len(rows)} из {total}")
        self._on_selection(self.view.selected_ids())

    def set_rows(self, rows: list[dict], title: str) -> None:
        """Show an arbitrary result (similar tracks, playlist) in the table."""
        self.mode_title = title
        self.model.set_rows(rows)
        self.count_label.setText(f"{title}: {len(rows)}")

    def reset_filters(self) -> None:
        self.mode_title = ""
        self.search.clear()
        for c in (self.genre, self.mood):
            c.setCurrentIndex(0)
        self.limit_plays.setChecked(False)
        self.stale.setChecked(False)
        self.refresh()

    def set_filters(self, text: str | None = None, genre: str | None = None, mood: str | None = None,
                    sort: str | None = None) -> None:
        if text is not None:
            self.search.setText(text)
        if genre is not None:
            self.genre.setCurrentIndex(max(self.genre.findText(genre), 0))
        if mood is not None:
            self.mood.setCurrentIndex(max(self.mood.findText(mood), 0))
        if sort is not None:
            self.sort.setCurrentIndex(max(self.sort.findText(sort), 0))
        self.mode_title = ""
        self.refresh()

    # ---- selection / actions -----------------------------------------
    def _tracks_changed(self, ids: list[int]) -> None:
        if not ids or self.ctx.closed:
            return
        by_id = {t["id"]: i for i, t in enumerate(self.model.rows)}
        for tid in ids:
            if tid in by_id:
                self.model.rows[by_id[tid]] = self.ctx.db.get_track(tid)
                self.model.dataChanged.emit(self.model.index(by_id[tid], 0),
                                            self.model.index(by_id[tid], self.model.columnCount() - 1))
        self._on_selection(self.view.selected_ids())

    def _on_selection(self, ids: list[int]) -> None:
        rows = {t["id"]: t for t in self.model.rows}
        self._selection = [rows[i] for i in ids if i in rows]
        sel = self._selection
        for w in (self.btn_play, self.btn_queue, self.btn_next, self.btn_pl, self.genre_apply, self.genre_edit):
            w.setEnabled(bool(sel))
        self.btn_similar.setEnabled(len(sel) == 1)
        self.chips.show_state(sel)
        self.flag_chips.show_state(sel)
        self.lv_chips.show_state(sel)
        self.genre_chips.show_state(sel)
        self.era_chips.show_state(sel)
        self.btn_trash.setEnabled(bool(sel))
        self.btn_edit.setEnabled(bool(sel))
        fav = bool(sel) and all(self.ctx.db.is_favorite("track", str(t["id"])) for t in sel)
        self.btn_fav.setText("♥ В избранном (убрать)" if fav else "♡ В избранное")
        self.btn_fav.setEnabled(bool(sel))
        from musicplayer.core import labelclip
        self.btn_copy.setEnabled(len(sel) == 1)
        self.btn_paste.setEnabled(bool(sel) and self.ctx.label_clipboard is not None)
        self.btn_paste.setToolTip(labelclip.describe(self.ctx.label_clipboard) +
                                  ("\nЗаменит метки у всех выбранных треков" if len(sel) > 1 else ""))
        while self.sugg_box.count():
            it = self.sugg_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not sel:
            self.cover.setPixmap(self.covers.pixmap(None, int(200 * getattr(self, "_zoom", 1.0))))
            self.info.setText("Выберите треки в таблице")
            self.genre_edit.clear()
            self.sugg_title.hide()
            return
        t = sel[0]
        self.cover.setPixmap(self.covers.pixmap(t["cover_key"], int(210 * getattr(self, "_zoom", 1.0))))
        if len(sel) == 1:
            self.info.setText(f"<b>{t['title']}</b><br>{t['artist']}<br>{t['album'] or '—'} · {t['year'] or '—'}<br>"
                              f"Прослушано: {t['play_count']} (30 дн: {t['plays_by_period']['month']}, "
                              f"год: {t['plays_by_period']['year']}), последний раз {fmt_date(t['last_played'])}")
            self.genre_edit.setText(", ".join(t["genres"]))
        else:
            self.info.setText(f"<b>Выбрано треков: {len(sel)}</b>")
            common = set(sel[0]["genres"]).intersection(*[set(s["genres"]) for s in sel[1:]])
            self.genre_edit.setText(", ".join(g for g in sel[0]["genres"] if g in common))
        sugg = {}
        for s in sel:
            for m in s["mood_suggestions"]:
                sugg.setdefault(m["mood"], []).append(m)
        self.sugg_title.setVisible(bool(sugg))
        for mood, items in sugg.items():
            conf = sum(i["confidence"] or 0 for i in items) / len(items)
            src = items[0]["source"]
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(QLabel(f"{mood}  {int(conf * 100)}% · {src}" + (f" ×{len(items)}" if len(sel) > 1 else "")), 1)
            ok, no = QPushButton("✓"), QPushButton("✗")
            ok.setFixedWidth(32)
            no.setFixedWidth(32)
            ok.clicked.connect(lambda _=False, m=mood: self.resolve(m, True))
            no.clicked.connect(lambda _=False, m=mood: self.resolve(m, False))
            h.addWidget(ok)
            h.addWidget(no)
            self.sugg_box.addWidget(row)

    def resolve(self, mood: str, accept: bool) -> None:
        ids = [t["id"] for t in self._selection]
        self.ctx.db.resolve_suggestions(ids, accept=accept, moods=[mood])
        self.ctx.events.tracks_changed.emit(ids)

    def _toggle_mood(self, mood: str, wanted: bool) -> None:
        ids = [t["id"] for t in self._selection]
        if not ids:
            return
        self.ctx.db.set_moods(ids, [mood], "add" if wanted else "remove")
        self.chips.set_counts(self.ctx.db.mood_example_counts())
        self.ctx.events.tracks_changed.emit(ids)

    def _toggle_genre(self, genre: str, wanted: bool) -> None:
        ids = [t["id"] for t in self._selection]
        if ids:
            self.ctx.db.set_genres(ids, [genre], "add" if wanted else "remove")
            self.ctx.events.tracks_changed.emit(ids)

    def _set_era(self, decade) -> None:
        ids = [t["id"] for t in self._selection]
        if ids:
            self.ctx.db.set_era(ids, decade)
            self.ctx.events.tracks_changed.emit(ids)

    def toggle_favorite(self) -> None:
        sel = self._selection
        if not sel:
            return
        on = not all(self.ctx.db.is_favorite("track", str(t["id"])) for t in sel)
        for t in sel:
            self.ctx.db.set_favorite("track", str(t["id"]), on)
        self.ctx.events.favorites_changed.emit()

    def edit_selected(self) -> None:
        from musicplayer.ui.edittrack import EditTrackDialog
        if self._selection:
            EditTrackDialog(self.ctx, list(self._selection), self).exec()

    def copy_labels(self) -> None:
        from musicplayer.core import labelclip
        if len(self._selection) == 1:
            self.ctx.label_clipboard = labelclip.snapshot(self.ctx.db, self._selection[0]["id"])
            self._tracks_changed([self._selection[0]["id"]])

    def paste_labels(self) -> None:
        from musicplayer.core import labelclip
        ids = [t["id"] for t in self._selection]
        if ids and self.ctx.label_clipboard:
            labelclip.paste(self.ctx.db, ids, self.ctx.label_clipboard)
            self.ctx.events.tracks_changed.emit(ids)

    def trash_selected(self) -> None:
        confirm_trash(self, self.ctx, list(self._selection))

    def _set_lv(self, field: str, value) -> None:
        ids = [t["id"] for t in self._selection]
        if ids:
            (self.ctx.db.set_language if field == "lang" else self.ctx.db.set_vocal)(ids, value, src="manual")
            self.ctx.events.tracks_changed.emit(ids)

    def _toggle_flag(self, flag: str, wanted: bool) -> None:
        ids = [t["id"] for t in self._selection]
        if ids:
            self.ctx.db.set_flags(ids, [flag], "add" if wanted else "remove")
            self.ctx.events.tracks_changed.emit(ids)

    def _apply_genres(self) -> None:
        ids = [t["id"] for t in self._selection]
        genres = [g.strip() for g in self.genre_edit.text().split(",") if g.strip()]
        self.ctx.db.set_genres(ids, genres)
        self.ctx.events.tracks_changed.emit(ids)

    def play_row(self, row: int) -> None:
        self.ctx.player.play_tracks(self.model.rows, row)

    def play_selected(self) -> None:
        if self._selection:
            rows = self.model.rows
            first = rows.index(self._selection[0]) if len(self._selection) == 1 else 0
            self.ctx.player.play_tracks(rows if len(self._selection) == 1 else self._selection, first)

    def show_similar(self) -> None:
        if len(self._selection) == 1:
            t = self._selection[0]
            res = similar_tracks(self.ctx.db, t["id"], 60)
            self.set_rows(res, f"Похожие на «{t['title']}»")

    def show_track(self, tid: int) -> None:
        self.reset_filters()
        self.view.select_ids([tid])
