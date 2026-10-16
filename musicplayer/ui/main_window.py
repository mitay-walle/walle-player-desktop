"""Main window: page navigation, job progress bar, player bar."""

from __future__ import annotations

from PySide6.QtCore import QSize, QEvent, QObject, QTimer, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QScrollArea, QStyle, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QProgressBar, QPushButton,
                               QSlider, QStackedWidget, QVBoxLayout, QWidget)

from musicplayer import __version__
from musicplayer.core import autotag, mood, scanner
from musicplayer.core.paths import resource_dir
from musicplayer.ui.icons import icon, make_icon_button
from musicplayer.ui.context import AppContext
from musicplayer.ui.jobs import JobInfo
from musicplayer.ui.miniplayer import MiniPlayer
from musicplayer.ui.pages.albums import AlbumsPage
from musicplayer.ui.pages.colormusic import ColorMusic, ColorMusicPage
from musicplayer.ui.pages.labelfeed import LabelFeedPage
from musicplayer.ui.pages.library import LibraryPage
from musicplayer.ui.pages.moods import MoodsPage
from musicplayer.ui.pages.playlists import PlaylistsPage, playlist_menu
from musicplayer.ui.pages.artists import ArtistsPage
from musicplayer.ui.pages.favorites import FavoritesPage
from musicplayer.ui.pages.history import HistoryPage
from musicplayer.ui.pages.queue import QueuePage
from musicplayer.ui.pages.stats import StatsPage
from musicplayer.ui.pages.wave import WavePage
from musicplayer.ui.theme import TOKENS, apply_theme
from musicplayer.ui.widgets import CoverCache, JumpSlider, fmt_time

PAGES = [("wave", "Моя волна"), ("library", "Библиотека"), ("artists", "Исполнители"), ("albums", "Альбомы"), ("favorites", "Избранное"), ("playlists", "Плейлисты"),
         ("queue", "Очередь"), ("history", "История"), ("labelfeed", "Разметка"), ("moods", "Модель настроений"), ("colormusic", "Цветомузыка"),
         ("stats", "Статистика")]
NAV_ICONS = {"wave": "🌊", "library": "♫", "artists": "☺", "albums": "▦", "playlists": "☰", "queue": "≡", "favorites": "♥", "history": "◷", "labelfeed": "🏷",
             "moods": "◔", "colormusic": "✺", "stats": "▤"}
NAV_SVG = {"wave": "play", "library": "queue", "artists": "mic", "albums": "album", "playlists": "list",
           "favorites": "heart", "queue": "next", "history": "clock", "labelfeed": "tag", "moods": "chart",
           "colormusic": "spark", "stats": "bars"}
ZOOMS = [70, 85, 100, 115, 130, 150, 175, 200]
SHUFFLE_LABEL = {"off": "⇄ выкл", "random": "⇄ случайно", "rare": "⇄ редкие"}
REPEAT_LABEL = {"off": "↻ выкл", "all": "↻ всё", "one": "↻ трек"}


def app_icon():
    from PySide6.QtGui import QIcon
    return QIcon(str(resource_dir() / "icon.ico"))


class SeekSlider(QSlider):
    """Progress slider: a click anywhere on the groove jumps there, dragging scrubs, release seeks."""

    seek_requested = Signal(int)          # 0..1000

    def __init__(self) -> None:
        super().__init__(Qt.Orientation.Horizontal)
        self.setRange(0, 1000)
        self.dragging = False
        self.setMinimumHeight(36)                 # easy to hit: the whole 26 px band is clickable, the line stays thin
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _value_at(self, x: float) -> int:
        return QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), int(x), max(1, self.width()))

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.setValue(self._value_at(e.position().x()))
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self.dragging:
            self.setValue(self._value_at(e.position().x()))
            e.accept()

    def mouseReleaseEvent(self, e) -> None:
        if self.dragging and e.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self.setValue(self._value_at(e.position().x()))
            self.seek_requested.emit(self.value())
            e.accept()

    def keyPressEvent(self, e) -> None:
        step = {Qt.Key.Key_Left: -20, Qt.Key.Key_Right: 20}.get(e.key())
        if step is None:
            return super().keyPressEvent(e)
        self.setValue(max(0, min(1000, self.value() + step)))
        self.seek_requested.emit(self.value())


class PlayerBar(QWidget):
    def __init__(self, ctx: AppContext, covers: CoverCache) -> None:
        super().__init__()
        self.ctx, self.covers = ctx, covers
        self.setStyleSheet(f"PlayerBar {{ background:{TOKENS['surface']}; border-top:1px solid {TOKENS['border']}; }}")
        p = ctx.player
        self.cover = QLabel()
        self.cover.setFixedSize(64, 64)
        self.title = QLabel("Ничего не играет")
        self.title.setStyleSheet("font-weight:600; font-size:13px;")
        self.sub = QLabel()
        self.sub.setStyleSheet(f"color:{TOKENS['text_muted']}")
        self.counted = QLabel()
        self.counted.setStyleSheet(f"color:{TOKENS['success']}")
        info = QVBoxLayout()
        info.addWidget(self.title)
        info.addWidget(self.sub)
        info.addWidget(self.counted)
        self.btn_prev, self.btn_play, self.btn_next = QPushButton(), QPushButton(), QPushButton()
        make_icon_button(self.btn_prev, "prev", TOKENS["text"], 24, "Предыдущий")
        make_icon_button(self.btn_next, "next", TOKENS["text"], 24, "Следующий")
        self.btn_play.setFixedSize(48, 48)          # the design kit's round Ochre play button, a vector triangle
        self.btn_play.setIconSize(QSize(24, 24))
        self.btn_play.setIcon(icon("play", TOKENS["bg"], 24))
        self.btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play.setStyleSheet(f"QPushButton {{ background:{TOKENS['accent']}; border-radius:24px; padding:0; border:none;"
                                    f" min-height:48px; max-height:48px; min-width:48px; max-width:48px; }}"
                                    f"QPushButton:hover {{ background:#E0B33F; }}")
        self.btn_fav = QPushButton()
        make_icon_button(self.btn_fav, "heart", TOKENS["text_muted"], 22, "В избранное (F)")
        self.seek = SeekSlider()
        self.time = QLabel("0:00 / 0:00")
        self.btn_shuffle = QPushButton()
        self.btn_repeat = QPushButton()
        make_icon_button(self.btn_shuffle, "shuffle", TOKENS["text_muted"], 22)
        make_icon_button(self.btn_repeat, "repeat", TOKENS["text_muted"], 22)
        self.btn_hide = QPushButton()
        make_icon_button(self.btn_hide, "hide", TOKENS["text_muted"], 22, "Не показывать: этот трек, альбом или исполнителя")
        self.btn_tags = QPushButton()
        make_icon_button(self.btn_tags, "tag", TOKENS["text_muted"], 22)
        self.btn_tags.setToolTip("Разметить играющий трек: настроения и тип записи (Ctrl+L)")
        self.btn_mini = QPushButton()
        make_icon_button(self.btn_mini, "mini", TOKENS["text_muted"], 22)
        self.btn_mini.setToolTip("Мини-плеер (Ctrl+M)")
        self.vol = JumpSlider(Qt.Orientation.Horizontal)
        self.vol.setMinimumHeight(36)                 # a tall clickable band; the line itself stays thin
        self.vol.setCursor(Qt.CursorShape.PointingHandCursor)
        self.vol.setRange(0, 100)
        self.vol.setValue(int(p.volume * 100))
        self.vol.setFixedWidth(110)
        self.strip = ColorMusic(ctx, covers, strip=True)
        self.strip.setVisible(ctx.settings.color_strip)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.title_row = QWidget()                    # narrow window: the title moves here, above the buttons
        self._title_lay = QHBoxLayout(self.title_row)
        self._title_lay.setContentsMargins(9, 6, 9, 0)
        outer.addWidget(self.title_row)
        self.title_row.hide()
        self._info = info
        row = QWidget()
        outer.addWidget(row)
        lay = QHBoxLayout(row)
        lay.addWidget(self.cover)
        lay.addLayout(info, 2)
        lay.addWidget(self.btn_fav)
        lay.addWidget(self.btn_hide)
        lay.addWidget(self.strip)                     # small equaliser next to the title, not a full-width band
        lay.addWidget(self.btn_shuffle)
        for b in (self.btn_prev, self.btn_play, self.btn_next):
            lay.addWidget(b)
        lay.addWidget(self.btn_repeat)
        lay.addWidget(self.seek, 5)
        lay.addWidget(self.time)
        lay.addWidget(self.btn_tags)
        lay.addWidget(self.btn_mini)
        self.vol_icon = QLabel()
        self.vol_icon.setPixmap(icon("volume", TOKENS["text_muted"], 20).pixmap(20, 20))
        lay.addWidget(self.vol_icon)
        lay.addWidget(self.vol)
        self._row_lay = lay
        # narrow window: the title gets the whole first line, the seek bar its own line under the buttons
        self.seek_row = QWidget()
        sr = QHBoxLayout(self.seek_row)
        sr.setContentsMargins(9, 0, 9, 6)
        self._seek_lay = sr
        outer.addWidget(self.seek_row)
        self.seek_row.hide()
        self._narrow = False
        self.btn_prev.clicked.connect(p.prev)
        self.btn_next.clicked.connect(p.next)
        self.btn_play.clicked.connect(p.toggle)
        self.btn_tags.clicked.connect(self.labels_menu)
        self.btn_fav.clicked.connect(self.toggle_favorite)
        from musicplayer.ui.hideactions import current_track_menu
        self.btn_hide.clicked.connect(lambda: current_track_menu(self.ctx, self.btn_hide))
        ctx.events.favorites_changed.connect(lambda: self._show_fav(self.ctx.player.current))
        for w in (self.cover, self.title):           # click on the cover / title: show the track in the library
            w.setCursor(Qt.CursorShape.PointingHandCursor)
            w.setToolTip("Показать трек в библиотеке")
            w.installEventFilter(self)
        from musicplayer.ui.tracklinks import make_link_label
        make_link_label(self.sub, ctx, lambda: self.ctx.player.current)   # artist / album: their tracks
        self.btn_shuffle.clicked.connect(self.cycle_shuffle)
        self.btn_repeat.clicked.connect(self.cycle_repeat)
        self.vol.valueChanged.connect(lambda v: p.set_volume(v / 100))
        self.seek.seek_requested.connect(self._seek_requested)
        p.track_changed.connect(self.on_track)
        p.state_changed.connect(lambda s: self.btn_play.setIcon(icon("pause" if s == "playing" else "play", TOKENS["bg"], 24)))
        p.position_changed.connect(self.on_position)
        p.play_counted.connect(lambda tid: self.counted.setText("✓ прослушивание засчитано"))
        p.queue_changed.connect(self.update_modes)
        self.on_track(None)

    def eventFilter(self, obj, ev) -> bool:
        if obj in (self.cover, self.title) and ev.type() == QEvent.Type.MouseButtonRelease                 and ev.button() == Qt.MouseButton.LeftButton:
            self.reveal_current()
            return True
        return super().eventFilter(obj, ev)

    def reveal_current(self) -> None:
        t = self.ctx.player.current
        if t is None:
            return
        self.ctx.events.navigate.emit("library")
        self.ctx.events.show_track.emit(t["id"])

    def minimumSizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(360, super().minimumSizeHint().height())     # secondary controls hide when it gets narrow

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        w = e.size().width()
        for widget, need in ((self.btn_shuffle, 760), (self.btn_repeat, 760), (self.vol, 900), (self.vol_icon, 900),
                             (self.time, 560)):
            widget.setVisible(w >= need)
        self.strip.setVisible(self.ctx.settings.color_strip and w >= 700)
        self.cover.setVisible(w >= 520)

        self.btn_mini.setVisible(w >= 400)

        self.counted.setVisible(w >= 760)
        self._set_narrow(w < 760)
        if self._narrow:
            self.time.setVisible(True)

    def _set_narrow(self, narrow: bool) -> None:
        """Narrow: seek bar + time move to their own full-width line, the title is not squeezed by the buttons."""
        if narrow == self._narrow:
            return
        self._narrow = narrow
        if narrow:
            self._row_lay.removeItem(self._info)
            self._title_lay.addLayout(self._info, 1)
            self.title_row.show()
            for wdg in (self.seek, self.time):
                self._row_lay.removeWidget(wdg)
            self._seek_lay.addWidget(self.seek, 1)
            self._seek_lay.addWidget(self.time)
            self.time.setVisible(True)
            self.seek_row.show()
        else:
            self._title_lay.removeItem(self._info)
            self._row_lay.insertLayout(self._row_lay.indexOf(self.cover) + 1, self._info, 2)
            self.title_row.hide()
            for wdg in (self.seek, self.time):
                self._seek_lay.removeWidget(wdg)
            i = self._row_lay.indexOf(self.btn_next) + 1
            self._row_lay.insertWidget(i, self.seek, 5)
            self._row_lay.insertWidget(i + 1, self.time)
            self.seek_row.hide()

    def apply_zoom(self, f: float) -> None:
        self.cover.setFixedSize(int(64 * f), int(64 * f))
        self.vol.setFixedWidth(int(110 * f))
        self.strip.setFixedSize(int(110 * f), int(30 * f))
        t = self.ctx.player.current
        self.on_track(t)

    def _popup(self):
        from musicplayer.ui.tagpopup import TagPopup
        if not hasattr(self, "_tags"):
            self._tags = TagPopup(self.ctx, self)
        return self._tags

    def open_tags(self) -> None:
        """Ctrl+L: the labels of the playing track at once."""
        self._popup().open_for(self.ctx.player.current, self.btn_tags)

    def labels_menu(self) -> None:
        """The tag button: labels of the track, its album or its artist."""
        from musicplayer.ui.tagpopup import labels_menu
        labels_menu(self.ctx, self.btn_tags, self._popup())

    def update_modes(self) -> None:
        q = self.ctx.player.queue
        on, off = TOKENS["accent"], TOKENS["text_muted"]     # the mode shows as colour (Ochre = on), details in the tooltip
        self.btn_shuffle.setIcon(icon("shuffle", off if q.shuffle == "off" else on, 22))
        self.btn_shuffle.setToolTip("Перемешивание: " + {"off": "выкл", "random": "случайно", "rare": "случайно, чаще редкие"}[q.shuffle]
                                    + " (клик — следующий режим)")
        self.btn_repeat.setIcon(icon("repeat_one" if q.repeat == "one" else "repeat", off if q.repeat == "off" else on, 22))
        self.btn_repeat.setToolTip("Повтор: " + {"off": "выкл", "all": "вся очередь", "one": "один трек"}[q.repeat]
                                   + " (клик — следующий режим)")

    def cycle_shuffle(self) -> None:
        modes = ["off", "random", "rare"]
        self.ctx.player.set_shuffle(modes[(modes.index(self.ctx.player.queue.shuffle) + 1) % 3])

    def cycle_repeat(self) -> None:
        modes = ["off", "all", "one"]
        self.ctx.player.set_repeat(modes[(modes.index(self.ctx.player.queue.repeat) + 1) % 3])

    def toggle_favorite(self) -> None:
        t = self.ctx.player.current
        if t is None:
            return
        key = str(t["id"])
        self.ctx.set_liked("track", key, not self.ctx.is_liked("track", key))
        self.ctx.events.favorites_changed.emit()

    def _show_fav(self, t: dict | None) -> None:
        on = bool(t) and self.ctx.is_liked("track", str(t["id"]))
        self.btn_fav.setIcon(icon("heart_on" if on else "heart", TOKENS["accent"] if on else TOKENS["text_muted"], 22))
        who = f" — слушатель «{self.ctx.listener_name}»" if self.ctx.listener_name else ""
        self.btn_fav.setToolTip(("Убрать из избранного (F)" if on else "В избранное (F)") + who)
        self.btn_fav.setEnabled(t is not None)
        self.btn_hide.setEnabled(t is not None)

    def on_track(self, t: dict | None) -> None:
        self._show_fav(t)
        self.counted.setText("")
        if t is None:
            self.title.setText("Ничего не играет")
            self.sub.setText("")
            self.cover.setPixmap(self.covers.pixmap(None, 64))
            self.time.setText("0:00 / 0:00")
            self.seek.setValue(0)
            return
        self.title.setText(t["title"])
        from musicplayer.ui import tracklinks
        self.sub.setText(tracklinks.html(t))
        self.cover.setPixmap(self.covers.pixmap(t["cover_key"], 64))

    def on_position(self, pos: float, dur: float) -> None:
        self.time.setText(f"{fmt_time(pos)} / {fmt_time(dur)}")
        if not self.seek.dragging and dur > 0:
            self.seek.setValue(int(pos / dur * 1000))

    def _seek_requested(self, value: int) -> None:
        self.ctx.player.seek_fraction(value / 1000)


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(f"Walle Player {__version__}")
        self.setWindowIcon(app_icon())
        self.resize(1500, 900)
        self.setMinimumSize(420, 300)
        if ctx.settings.window_geom:
            from PySide6.QtCore import QByteArray
            self.restoreGeometry(QByteArray.fromHex(ctx.settings.window_geom.encode()))
        self.covers = CoverCache(ctx.covers)
        self.pages: dict[str, QWidget] = {
            "library": LibraryPage(ctx, self.covers), "albums": AlbumsPage(ctx, self.covers),
            "queue": QueuePage(ctx), "history": HistoryPage(ctx), "favorites": FavoritesPage(ctx), "artists": ArtistsPage(ctx), "playlists": PlaylistsPage(ctx, self.covers),
            "moods": MoodsPage(ctx), "stats": StatsPage(ctx),
            "colormusic": ColorMusicPage(ctx, self.covers), "wave": WavePage(ctx, self.covers),
        }
        self.pages["labelfeed"] = LabelFeedPage(ctx, self.covers, self.pages["library"])
        self.stack = QStackedWidget()
        for key, _ in PAGES:
            area = QScrollArea()                       # a narrow window scrolls the page instead of forcing a huge minimum
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setWidget(self.pages[key])
            self.stack.addWidget(area)

        # left sidebar (like Yandex Music) + a slim top bar with global search
        side = QFrame()
        self.side = side
        side.setObjectName("sidebar")
        side.setFixedWidth(210)
        side.setStyleSheet(f"QFrame#sidebar {{ background:{TOKENS['surface']}; border-right:1px solid {TOKENS['border']}; }}"
                           f"QFrame#sidebar QPushButton {{ text-align:left; padding:9px 14px; border:none; "
                           f"background:transparent; font-size:13px; }}"
                           f"QFrame#sidebar QPushButton:hover {{ background:{TOKENS['surface2']}; }}"
                           f"QFrame#sidebar QPushButton:checked {{ background:rgba(213,165,44,38); color:{TOKENS['accent']}; "
                           f"border-radius:8px; font-weight:600; }}")
        self._side_base = side.styleSheet()
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 10, 0, 10)
        from PySide6.QtGui import QPixmap
        logo = QLabel()
        logo.setPixmap(QPixmap(str(resource_dir() / "wordmark.png")).scaledToHeight(
            34, Qt.TransformationMode.SmoothTransformation))
        logo.setToolTip("Walle Player")
        logo.setStyleSheet("padding:4px 10px 14px 10px;")
        sv.addWidget(logo)
        self.logo = logo
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, label in PAGES:
            b = QPushButton(f"  {label}")
            b.setIcon(icon(NAV_SVG.get(key, "queue"), TOKENS["text_muted"], 20))
            b.setIconSize(QSize(20, 20))
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.set_page(k))
            sv.addWidget(b)
            self.nav_buttons[key] = b
        sv.addStretch(1)
        self.btn_folder = QPushButton("  Папка с музыкой…")
        self.btn_folder.setIcon(icon("folder", TOKENS["text_muted"], 20))
        self.btn_folder.setIconSize(QSize(20, 20))
        self.btn_folder.setToolTip(f"Сейчас: {ctx.settings.library_root}\nВыбрать папку и просканировать")
        self.btn_rescan = QPushButton("  Обновить библиотеку")
        self.btn_rescan.setIcon(icon("refresh", TOKENS["text_muted"], 20))
        self.btn_rescan.setIconSize(QSize(20, 20))
        self.btn_rescan.setToolTip("Пересканировать текущую папку (быстро, только изменения)")
        self.btn_share = QPushButton("  Другие устройства")
        self.btn_share.setIcon(icon("phone", TOKENS["text_muted"], 20))
        self.btn_share.setIconSize(QSize(20, 20))
        self.btn_share.setToolTip("Слушать библиотеку на телефоне и других компьютерах (Wi-Fi и интернет)")
        self.listener_box = QComboBox()                  # whose likes / «Не показывать» (guests of the library server)
        self.listener_box.setToolTip("За кого ставить лайки и «Не показывать»: вы или слушатель с телефона")
        self.listener_box.hide()
        sv.addWidget(self.listener_box)
        self.btn_marks = QPushButton()                   # what listeners marked «на удаление» (shown when there is any)
        self.btn_marks.setIcon(icon("trash", TOKENS["danger"], 20))
        self.btn_marks.setIconSize(QSize(20, 20))
        self.btn_marks.setToolTip("Треки и альбомы, которые слушатели пометили на удаление: удалить или оставить")
        self.btn_marks.hide()
        sv.addWidget(self.btn_marks)
        sv.addWidget(self.btn_share)
        sv.addWidget(self.btn_folder)
        sv.addWidget(self.btn_rescan)

        self.global_search = QLineEdit(placeholderText="🔍  Поиск по всей музыке: название, исполнитель, жанр, настроение…")
        self.global_search.setClearButtonEnabled(True)
        self.zoom_box = QComboBox()
        for z in ZOOMS:
            self.zoom_box.addItem(f"{z}%", z)
        self.zoom_box.setToolTip("Масштаб интерфейса (Ctrl+= / Ctrl+− / Ctrl+0, Ctrl+колесо)")
        nav = QHBoxLayout()
        nav.addWidget(self.global_search, 1)
        self.zoom_label = QLabel("Масштаб")
        nav.addWidget(self.zoom_label)
        nav.addWidget(self.zoom_box)

        # job bar
        self.job_label = QLabel()
        self.job_bar = QProgressBar()
        self.job_bar.setTextVisible(True)
        self.btn_cancel = QPushButton("Отмена")
        self.job_row = QWidget()
        jl = QHBoxLayout(self.job_row)
        jl.setContentsMargins(8, 2, 8, 2)
        jl.addWidget(self.job_label)
        jl.addWidget(self.job_bar, 1)
        jl.addWidget(self.btn_cancel)
        self.job_row.hide()

        self.player_bar = PlayerBar(ctx, self.covers)
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(side)
        right = QVBoxLayout()
        right.setContentsMargins(10, 8, 10, 0)
        right.addLayout(nav)
        right.addWidget(self.stack, 1)
        right.addWidget(self.job_row)
        body.addLayout(right, 1)
        self.body_w = QWidget()
        self.body_w.setLayout(body)
        outer.addWidget(self.body_w, 1)
        outer.addWidget(self.player_bar)
        self.setCentralWidget(central)

        self._search_timer = QTimer(self, singleShot=True, interval=200)
        self._search_timer.timeout.connect(self._global_search)
        self.global_search.textChanged.connect(lambda _t: self._search_timer.start())
        self.btn_folder.clicked.connect(self.browse)
        self.btn_rescan.clicked.connect(lambda: self.start_scan())
        from musicplayer.ui.share_dialog import RemoteSharing
        self.sharing = RemoteSharing(ctx)
        self.btn_share.clicked.connect(self.open_share)
        self.listener_box.currentIndexChanged.connect(self._listener_chosen)
        self.btn_marks.clicked.connect(self.open_delete_marks)
        self._marks_timer = QTimer(self, interval=15000)  # phones mark through the server: look now and then
        self._marks_timer.timeout.connect(self._update_marks)
        self._marks_timer.start()
        ctx.events.tracks_removed.connect(lambda ids: self._update_marks())
        ctx.events.delete_marks_changed.connect(self._update_marks)
        self._update_marks()
        self.sharing.changed.connect(lambda: QTimer.singleShot(0, self._fill_listeners))
        ctx.events.favorites_changed.connect(lambda: self.player_bar._show_fav(ctx.player.current))
        self._fill_listeners()
        self.sharing.join_requested.connect(self._join_request)
        if self.sharing.cfg.enabled:                         # the library was open for other devices at exit
            QTimer.singleShot(500, self._autostart_sharing)
        ctx.events.request_scan.connect(self.browse)
        ctx.events.request_autotag.connect(lambda: self.start_autotag() if not ctx.jobs.busy else None)
        self.btn_cancel.clicked.connect(ctx.jobs.cancel)
        ctx.jobs.started.connect(self._job_started)
        ctx.jobs.progress.connect(self._job_progress)
        ctx.jobs.finished.connect(self._job_finished)
        ctx.events.navigate.connect(self.set_page)
        ctx.player.error.connect(lambda m: self.statusBar().showMessage(f"Ошибка воспроизведения: {m}", 6000))
        self._hotkeys()
        QApplication.instance().installEventFilter(self)
        self.zoom = 1.0
        self.zoom_box.currentIndexChanged.connect(lambda i: self.set_zoom(ZOOMS[i] / 100))
        self.mini = MiniPlayer(ctx, self.covers)
        self.mini.expand_requested.connect(self.leave_mini)
        self.mini.moved.connect(self._mini_moved)
        self.player_bar.btn_mini.clicked.connect(self.enter_mini)
        self._session_timer = QTimer(self, interval=5000)
        self._session_timer.timeout.connect(self.save_session)
        self._session_timer.start()
        ctx.player.track_changed.connect(lambda _t: QTimer.singleShot(300, self.save_session))
        QTimer.singleShot(0, self.restore_session)
        self._ext_timer = QTimer(self, interval=1000)          # training / tagging run by a script in the background
        self._ext_timer.timeout.connect(self._poll_external_job)
        self._ext_timer.start()
        self._ext_shown = False
        self.set_zoom(ctx.settings.ui_zoom)
        self.set_page("wave")

    COMPACT_BELOW = 820

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._adapt_layout()

    def _adapt_layout(self) -> None:
        """Narrow window: the sidebar shrinks to icons, the zoom control and the logo go away."""
        w = self.width()
        compact = w < self.COMPACT_BELOW
        self.side.setFixedWidth(46 if compact else 210)
        self.logo.setVisible(not compact)
        self.zoom_label.setVisible(w >= 560)
        self.zoom_box.setVisible(w >= 560)
        for key, label in PAGES:
            b = self.nav_buttons[key]
            b.setText("" if compact else f"  {label}")
            b.setToolTip(label if compact else "")
        self.btn_folder.setText("" if compact else "  Папка с музыкой…")
        self.btn_rescan.setText("" if compact else "  Обновить библиотеку")
        self.btn_share.setText("" if compact else "  Другие устройства")
        css = "padding:9px 0; text-align:center;" if compact else "padding:9px 14px; text-align:left;"
        self.side.setStyleSheet(self._side_base + f"QFrame#sidebar QPushButton {{ {css} }}")

    def _hotkeys(self) -> None:
        p = self.ctx.player
        for seq, fn in (("Ctrl+Right", p.next), ("Ctrl+Left", p.prev), ("Ctrl+Space", p.toggle),
                        ("F", lambda: self.player_bar.toggle_favorite()),
                        ("Ctrl+L", lambda: self.player_bar.open_tags()),
                        ("Ctrl+M", lambda: self.leave_mini() if self.mini.isVisible() else self.enter_mini()),
                        ("Ctrl+=", lambda: self.zoom_step(1)), ("Ctrl++", lambda: self.zoom_step(1)),
                        ("Ctrl+-", lambda: self.zoom_step(-1)), ("Ctrl+0", lambda: self.set_zoom(1.0)),
                        ("Ctrl+F", lambda: self.global_search.setFocus())):
            QShortcut(QKeySequence(seq), self, activated=fn)

    # ---- session: queue, current track and position survive a restart --------------------------
    def offer_undo(self, text: str, undo) -> None:
        """A message in the status bar with «Отменить» for a few seconds (after «Не показывать» and the like)."""
        from PySide6.QtWidgets import QPushButton
        btn = getattr(self, "_undo_btn", None)
        if btn is None:
            btn = self._undo_btn = QPushButton("Отменить")
            btn.setObjectName("Ghost")
            self.statusBar().addPermanentWidget(btn)
            self._undo_timer = QTimer(self, singleShot=True, interval=10000)
            self._undo_timer.timeout.connect(lambda: (btn.hide(), self.statusBar().clearMessage()))
        try:
            btn.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        btn.clicked.connect(lambda: (undo(), btn.hide(), self.statusBar().showMessage("Отменено", 3000)))
        btn.show()
        self.statusBar().showMessage(text, 10000)
        self._undo_timer.start()

    def save_session(self) -> None:
        if self.ctx.closed:
            return
        p = self.ctx.player
        q = p.queue
        if not q.items or p.current is None:          # nothing playing: still keep the chosen wave moods / character
            self.ctx.settings.wave = {**self.pages["wave"].session_state([]), "active": False}
            self.ctx.save_settings()
            return
        ids = [t["id"] for t in q.items]
        cur = q.current_index or 0
        lo = max(0, cur - 200)                        # keep it small: 200 back, 300 ahead
        self.ctx.settings.last_queue = ids[lo:cur + 300]
        self.ctx.settings.last_index = cur - lo
        try:
            self.ctx.settings.last_pos = getattr(p, "_cue_pos", 0.0) or float(p.deck.player.position()) / 1000
        except Exception:
            self.ctx.settings.last_pos = 0.0
        self.ctx.settings.wave = self.pages["wave"].session_state(q.items[lo:cur + 300])
        self.ctx.save_settings()

    def restore_session(self) -> None:
        s = self.ctx.settings
        if self.ctx.closed:
            return
        if not s.last_queue or self.ctx.player.current is not None:
            self.pages["wave"].restore_session({**(s.wave or {}), "active": False} if s.wave else None, [])
            return
        tracks = [t for t in (self.ctx.db.get_track(i) for i in s.last_queue) if t]
        if not tracks:
            return
        idx = min(max(0, s.last_index), len(tracks) - 1)
        tracks = self.pages["wave"].restore_session(s.wave, tracks)   # the wave goes on where it stopped
        if hasattr(self.ctx.player, "cue"):
            self.ctx.player.cue(tracks, idx, s.last_pos)

    # ---- mini player ----------------------------------------------------
    def enter_mini(self) -> None:
        """Hide the full window, show the tiny always-on-top player where it was last (or in the screen corner)."""
        self._normal_geometry = self.geometry()
        pos = self.ctx.settings.mini_pos
        if pos:
            self.mini.move(pos[0], pos[1])
        else:
            scr = self.screen().availableGeometry()
            self.mini.move(scr.right() - self.mini.width() - 24, scr.bottom() - self.mini.height() - 24)
        self.mini.show()
        self.hide()

    def _mini_moved(self, p) -> None:
        """Dragged: the mini player's place is kept at once (a crash or a Windows restart does not lose it)."""
        self.ctx.settings.mini_pos = [p.x(), p.y()]
        self.ctx.save_settings()

    def leave_mini(self) -> None:
        p = self.mini.pos()
        self.ctx.settings.mini_pos = [p.x(), p.y()]
        self.mini.hide()
        self.player_bar.vol.setValue(round(self.ctx.player.volume * 100))   # the mini player may have changed it
        self.show()
        self.raise_()
        self.activateWindow()

    # ---- zoom ---------------------------------------------------------
    def zoom_step(self, d: int) -> None:
        i = min(range(len(ZOOMS)), key=lambda k: abs(ZOOMS[k] - self.zoom * 100))
        self.set_zoom(ZOOMS[max(0, min(len(ZOOMS) - 1, i + d))] / 100)

    def set_zoom(self, f: float) -> None:
        """Scale fonts, rows, covers and panels of the whole UI (0.7 .. 2.0); saved in settings."""
        f = max(0.7, min(2.0, float(f)))
        self.zoom = f
        self.ctx.settings.ui_zoom = f
        apply_theme(QApplication.instance(), {**TOKENS, "font_size": max(7, round(TOKENS["font_size"] * f))})
        for page in self.pages.values():
            if hasattr(page, "apply_zoom"):
                page.apply_zoom(f)
        self.player_bar.apply_zoom(f)
        i = min(range(len(ZOOMS)), key=lambda k: abs(ZOOMS[k] - f * 100))
        self.zoom_box.blockSignals(True)
        self.zoom_box.setCurrentIndex(i)
        self.zoom_box.blockSignals(False)

    def eventFilter(self, obj: QObject, ev) -> bool:
        if ev.type() == QEvent.Type.Wheel and ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            w = obj
            while w is not None:      # the album grid uses Ctrl+wheel for tile size itself
                if w is self.pages["albums"].grid:
                    return False
                w = w.parent() if hasattr(w, "parent") else None
            self.zoom_step(1 if ev.angleDelta().y() > 0 else -1)
            return True
        return False

    # ---- navigation ---------------------------------------------------
    def set_page(self, key: str) -> None:
        keys = [k for k, _ in PAGES]
        if key not in keys:
            return
        self.stack.setCurrentIndex(keys.index(key))
        for k, b in self.nav_buttons.items():
            b.setChecked(k == key)

    def current_page_key(self) -> str:
        return PAGES[self.stack.currentIndex()][0]

    # ---- scan ---------------------------------------------------------
    def browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Папка с музыкой", self.ctx.settings.library_root)
        if d:
            self.start_scan(d)

    def _global_search(self) -> None:
        text = self.global_search.text().strip()
        if text:
            self.set_page("library")
            self.pages["library"].set_filters(text=text)

    def start_scan(self, root: str | None = None, full: bool = False) -> JobInfo:
        ctx = self.ctx
        root = root or ctx.settings.library_root
        ctx.settings.library_root = root
        self.btn_folder.setToolTip(f"Сейчас: {root}\nВыбрать папку и просканировать")

        return ctx.jobs.run_process("scan", "Сканирование библиотеки", "musicplayer.core.procjobs:scan",
                                    {"db_path": str(ctx.db_path), "root": str(root), "covers": str(ctx.covers), "full": full})

    def maybe_autotag(self) -> JobInfo | None:
        """After a scan: let the trained model determine the mood of tracks it has not seen yet."""
        ctx = self.ctx
        if not ctx.settings.auto_tag or mood.load_model(ctx.models) is None or ctx.jobs.busy:
            return None
        if not autotag.pending_ids(ctx.db):
            return None
        return self.start_autotag()

    def start_autotag(self, ids: list[int] | None = None) -> JobInfo:
        ctx = self.ctx
        mode = ctx.settings.auto_mode

        return ctx.jobs.run_process("autotag", "Определение настроения новых треков", "musicplayer.core.procjobs:autotag",
                                    {"db_path": str(ctx.db_path), "models": str(ctx.models),
                                     "ids": list(ids) if ids is not None else None, "mode": mode})

    # ---- job bar ------------------------------------------------------
    def _poll_external_job(self) -> None:
        from musicplayer.core import extprogress
        if self.ctx.jobs.busy:
            return
        d = extprogress.read(self.ctx.home)
        if d is None:
            if self._ext_shown:
                self._ext_shown = False
                self.job_row.hide()
                self.btn_cancel.show()
                self.ctx.events.library_changed.emit()      # the script changed labels: refresh the pages
            return
        self._ext_shown = True
        self.btn_cancel.hide()                              # a separate process: cannot be cancelled from here
        self.job_label.setText(f"⚙ {d['title']}")
        if d["total"] > 0:
            self.job_bar.setRange(0, d["total"])
            self.job_bar.setValue(d["done"])
            self.job_bar.setFormat(f"{d['done']}/{d['total']}  {d['message']}")
        else:
            self.job_bar.setRange(0, 0)
        self.job_row.show()

    def _job_started(self, job: JobInfo) -> None:
        self.job_label.setText(job.title)
        self.job_bar.setRange(0, 0)
        self.job_row.show()

    def _job_progress(self, job: JobInfo) -> None:
        if job.total > 0:
            self.job_label.setText(job.title)
            self.job_bar.setRange(0, job.total)
            self.job_bar.setValue(job.done)
            pct = job.done * 100 // job.total
            self.job_bar.setFormat(f"{job.done} из {job.total} ({pct}%)" + (f"  ·  {job.message}" if job.message else ""))
        else:
            self.job_bar.setRange(0, 0)
            self.job_label.setText(f"{job.title}: {job.message or f'{job.done} файлов'}")

    def _job_finished(self, job: JobInfo) -> None:
        self.job_row.hide()
        msg = {"finished": "готово", "failed": f"ошибка: {job.error}", "cancelled": "отменено"}[job.state]
        self.statusBar().showMessage(f"{job.title}: {msg}", 8000)
        if job.kind == "scan" and job.state == "finished":
            self.ctx.events.library_changed.emit()
            self.maybe_autotag()
        elif job.kind == "autotag":
            self.ctx.events.tracks_changed.emit([])
            self.ctx.events.library_changed.emit()

    def _fill_listeners(self) -> None:
        """The listener chooser: shown when the library has guests; a removed guest falls back to the owner."""
        users = self.sharing.cfg.users
        box = self.listener_box
        box.blockSignals(True)
        box.clear()
        for u in users:
            box.addItem(f"♥ {u['name']}" + (" (вы)" if u.get("owner") else ""), "owner" if u.get("owner") else u["id"])
        i = box.findData(self.ctx.listener)
        box.setCurrentIndex(max(i, 0))
        box.blockSignals(False)
        box.setVisible(len(users) > 1)
        self._listener_chosen(box.currentIndex())

    def _listener_chosen(self, i: int) -> None:
        uid = self.listener_box.itemData(i) or "owner"
        name = self.listener_box.itemText(i).lstrip("♥ ").replace(" (вы)", "")
        self.ctx.set_listener(uid, name)

    def _update_marks(self) -> None:
        from musicplayer.core import deletemarks
        if self.ctx.closed:
            return
        n = deletemarks.count(self.ctx.db)
        self.btn_marks.setVisible(n > 0)
        self.btn_marks.setText("" if getattr(self, "_side_compact", False) else f"  На удаление ({n})")

    def open_delete_marks(self) -> None:
        from musicplayer.ui.deletemarks_dialog import DeleteMarksDialog
        names = {("owner" if u.get("owner") else u["id"]): u["name"] for u in self.sharing.cfg.users}
        DeleteMarksDialog(self.ctx, names, self).exec()
        self._update_marks()

    def open_share(self) -> None:
        from musicplayer.ui.share_dialog import ShareDialog
        ShareDialog(self.sharing, self).exec()

    def _join_request(self, req: dict) -> None:
        """A device asks for access: the owner decides in a small window (the app is brought to attention)."""
        from musicplayer.ui.share_dialog import JoinDialog
        dlg = JoinDialog(self.sharing, req, self)
        dlg.show()
        dlg.raise_()
        QApplication.alert(self)

    def _autostart_sharing(self) -> None:
        import logging
        logging.getLogger(__name__).info("opening the library for other devices (port %s)", self.sharing.cfg.port)
        if not self.sharing.start():
            logging.getLogger(__name__).error("library server not started: %s", self.sharing.server.error)
            self.statusBar().showMessage(f"Доступ для других устройств не открыт: {self.sharing.server.error}", 10000)

    def closeEvent(self, e) -> None:
        tray = getattr(self, "tray", None)
        if tray is not None and tray.available and self.sharing.running and not getattr(self, "quitting", False):
            e.ignore()                                     # the library stays open for phones: hide to the tray
            self.save_session()
            self.mini.hide()
            self.hide()
            tray.hid_window()
            return
        self.sharing.stop(remember=False)
        if self.mini.isVisible():
            p = self.mini.pos()
            self.ctx.settings.mini_pos = [p.x(), p.y()]
        self.mini.close()
        self.save_session()
        geom = self.saveGeometry()
        self.ctx.settings.window_geom = bytes(geom.toHex()).decode()
        self.ctx.close()
        super().closeEvent(e)
        if not QApplication.instance().quitOnLastWindowClosed():      # the tray keeps the app alive otherwise
            QApplication.instance().quit()
