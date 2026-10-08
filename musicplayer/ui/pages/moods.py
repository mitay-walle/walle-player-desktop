"""Mood model page: analyse audio -> train -> predict -> review suggestions, all with live feedback."""

from __future__ import annotations

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import QPointF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog,
                               QLabel, QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from musicplayer.core import clap, mood, moodcv, webtags
from musicplayer.ui.context import AppContext
from musicplayer.ui.jobs import JobInfo
from musicplayer.ui.theme import TOKENS


def backup_model(models_dir) -> None:
    """Keep the current model as models/history/mood_model-<date>.pkl before it is replaced."""
    import shutil
    import time
    from pathlib import Path
    cur = Path(models_dir) / "mood_model.pkl"
    if cur.exists():
        hist = Path(models_dir) / "history"
        hist.mkdir(exist_ok=True)
        shutil.copy2(cur, hist / time.strftime("mood_model-%Y%m%d-%H%M%S.pkl", time.localtime(cur.stat().st_mtime)))


def saved_models(home) -> list[tuple[str, "Path"]]:
    """[(label, path)] of the current model, its history and models saved by experiments, newest first."""
    import time
    from pathlib import Path
    home = Path(home)
    paths = [home / "models" / "mood_model.pkl"] + sorted((home / "models" / "history").glob("mood_model-*.pkl"), reverse=True)
    paths += sorted(p / "mood_model.pkl" for p in home.glob("models_*") if (p / "mood_model.pkl").exists())
    out = []
    for i, p in enumerate(q for q in paths if q.exists()):
        m = mood.load_model(p.parent) if p.name == "mood_model.pkl" else _load_pkl(p)
        info = getattr(m, "info", {}) or {}
        auc = (info.get("val") or {}).get("auc")
        when = time.strftime("%d.%m %H:%M", time.localtime(p.stat().st_mtime))
        name = "текущая" if i == 0 else ("эксперимент " + p.parent.name[7:] if p.parent.name.startswith("models_") else "прошлая")
        size = f" · {info.get('tracks', '?')} треков, {len(getattr(m, 'moods', []) or [])} настроений"
        out.append((f"{name} · {when}{size} · качество {auc:.2f}" if auc is not None else f"{name} · {when}{size}", p))
    return out


def _load_pkl(p):
    import pickle
    try:
        with open(p, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def quality_word(auc: float | None) -> tuple[str, str]:
    if auc is None:
        return "нет оценки", "#8A9099"
    if auc >= 0.8:
        return "хорошо", "#7BD88F"
    if auc >= 0.7:
        return "средне", "#E8C46B"
    if auc >= 0.6:
        return "слабо", "#E8A87C"
    return "не работает", "#F28B82"


def card(title: str) -> tuple[QFrame, QVBoxLayout]:
    f = QFrame()
    f.setStyleSheet(f"QFrame#card {{ background:{TOKENS['surface']}; border:1px solid {TOKENS['border']}; }}")
    f.setObjectName("card")
    v = QVBoxLayout(f)
    t = QLabel(title)
    t.setStyleSheet("font-weight:600; font-size:13px; border:none;")
    v.addWidget(t)
    return f, v


class TrainChart(QChartView):
    """Live loss / validation curves."""

    def __init__(self) -> None:
        super().__init__()
        self.chart_ = QChart()
        self.chart_.legend().setVisible(True)
        self.chart_.legend().setLabelColor(QColor(TOKENS["text_muted"]))
        self.chart_.setBackgroundVisible(False)
        self.chart_.setMargins(self.chart_.margins())
        self.series = {}
        for name, color in (("loss", TOKENS["danger"]), ("val F1", TOKENS["success"]), ("val AUC", TOKENS["accent"])):
            s = QLineSeries(name=name)
            s.setPen(QPen(QColor(color), 2))
            self.chart_.addSeries(s)
            self.series[name] = s
        self.ax = QValueAxis()
        self.ax.setRange(0, 10)
        self.ax.setLabelFormat("%d")
        self.ay = QValueAxis()
        self.ay.setRange(0, 1)
        for a in (self.ax, self.ay):
            a.setLabelsColor(QColor(TOKENS["text_muted"]))
            a.setGridLineColor(QColor(TOKENS["border"]))
        self.chart_.addAxis(self.ax, Qt.AlignmentFlag.AlignBottom)
        self.chart_.addAxis(self.ay, Qt.AlignmentFlag.AlignLeft)
        for s in self.series.values():
            s.attachAxis(self.ax)
            s.attachAxis(self.ay)
        self.setChart(self.chart_)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMinimumHeight(220)

    def reset(self, epochs: int) -> None:
        for s in self.series.values():
            s.clear()
        self.ax.setRange(0, max(epochs, 1))

    def add(self, m: dict) -> None:
        self.series["loss"].append(QPointF(m["epoch"], min(m["loss"], 1.5)))
        self.series["val F1"].append(QPointF(m["epoch"], m["val_f1"]))
        if m.get("val_auc") is not None:
            self.series["val AUC"].append(QPointF(m["epoch"], m["val_auc"]))
        self.ay.setRange(0, max(1.0, max(p.y() for p in self.series["loss"].points()) * 1.05))


class MoodsPage(QWidget):
    key = "moods"

    def __init__(self, ctx: AppContext, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.last_train: dict | None = None
        self.player_hint = None

        # moods column
        self.mood_list = QListWidget()
        self.btn_add = QPushButton("＋ Добавить")
        self.btn_rename = QPushButton("Переименовать")
        self.btn_del = QPushButton("Удалить")
        mv = QVBoxLayout()
        mv.addWidget(QLabel("<b>Настроения</b>  (подтверждено / предложено)"))
        mv.addWidget(self.mood_list, 1)
        mh = QHBoxLayout()
        for b in (self.btn_add, self.btn_rename, self.btn_del):
            mh.addWidget(b)
        mv.addLayout(mh)
        left = QWidget()
        left.setLayout(mv)

        # step 1
        c1, v1 = card("1. Анализ звука (дескрипторы Freesound)")
        self.analysis_label = QLabel()
        self.workers = QSpinBox(minimum=1, maximum=16, value=4)
        self.btn_an_labeled = QPushButton("Проанализировать размеченные")
        self.btn_an_all = QPushButton("Проанализировать всю библиотеку")
        v1.addWidget(self.analysis_label)
        h = QHBoxLayout()
        h.addWidget(QLabel("Процессов:"))
        h.addWidget(self.workers)
        h.addWidget(self.btn_an_labeled)
        h.addWidget(self.btn_an_all)
        h.addStretch(1)
        v1.addLayout(h)

        # step 1b: ready-made CLAP model
        cc, vc = card("1б. Готовая модель CLAP — второе мнение (работает и без ваших меток)")
        self.clap_label = QLabel()
        self.clap_label.setWordWrap(True)
        self.btn_clap_lab = QPushButton("Эмбеддинги CLAP: размеченные")
        self.btn_clap_all = QPushButton("Эмбеддинги CLAP: вся библиотека")
        self.btn_zero = QPushButton("Предсказать без обучения")
        self.btn_agree = QPushButton("Сверить с моими метками")
        self.btn_zero.setToolTip("Zero-shot: CLAP сопоставляет звук с текстовым описанием каждого настроения")
        vc.addWidget(self.clap_label)
        h = QHBoxLayout()
        h.addWidget(self.btn_clap_lab)
        h.addWidget(self.btn_clap_all)
        vc.addLayout(h)
        h = QHBoxLayout()
        h.addWidget(self.btn_zero)
        h.addWidget(self.btn_agree)
        vc.addLayout(h)

        # step 1c: tags and moods from the internet
        cw, vw = card("1в. Теги и настроения из интернета (MusicBrainz, AcousticBrainz, Last.fm)")
        self.web_label = QLabel()
        self.web_label.setWordWrap(True)
        self.btn_web_lab = QPushButton("Загрузить для размеченных")
        self.btn_web_all = QPushButton("Загрузить для всей библиотеки (~1 ч)")
        self.btn_web_sug = QPushButton("Предложить настроения из интернета")
        self.btn_web_agree = QPushButton("Сверить с моими метками")
        self.btn_web_sug.setToolTip("AcousticBrainz: готовые предсказания happy/sad/aggressive/relaxed/party для записей, "
                                    "которые есть в базе. Попадают в предложения, вы подтверждаете.")
        vw.addWidget(self.web_label)
        h = QHBoxLayout()
        h.addWidget(self.btn_web_lab)
        h.addWidget(self.btn_web_all)
        vw.addLayout(h)
        h = QHBoxLayout()
        h.addWidget(self.btn_web_sug)
        h.addWidget(self.btn_web_agree)
        vw.addLayout(h)

        # step 2
        c2, v2 = card("2. Обучение на ваших метках")
        self.train_label = QLabel()
        self.epochs = QSpinBox(minimum=5, maximum=1000, value=120)
        self.btn_train = QPushButton("Обучить модель")
        self.web_mode = QComboBox()
        for label, key in (("Интернет-теги: авто", "auto"), ("Интернет-теги: всегда", True), ("Интернет-теги: никогда", False)):
            self.web_mode.addItem(label, key)
        self.web_mode.setToolTip("Авто: теги берутся, только если ЧЕСТНАЯ проверка показывает явный прирост. "
                                 "«Всегда» — принудительно, результат всё равно покажет прирост или потерю.")
        self.features = QComboBox()
        for label, key in (("Признаки: авто", "auto"), ("Дескрипторы", "descriptors"), ("CLAP", "clap"),
                           ("Дескрипторы + CLAP", "both")):
            self.features.addItem(label, key)
        self.chart = TrainChart()
        self.metrics = QLabel()
        self.metrics.setTextFormat(Qt.TextFormat.RichText)
        self.metrics.setWordWrap(True)
        v2.addWidget(self.train_label)
        h = QHBoxLayout()
        h.addWidget(QLabel("Эпох:"))
        h.addWidget(self.epochs)
        h.addWidget(self.features)
        h.addWidget(self.web_mode)
        h.addWidget(self.btn_train)
        h.addStretch(1)
        v2.addLayout(h)
        v2.addWidget(self.chart)
        v2.addWidget(self.metrics)

        # step 3
        c3, v3 = card("3. Предсказание — результат попадает в «предложения», вы подтверждаете")
        self.threshold = QDoubleSpinBox(minimum=0.05, maximum=0.99, singleStep=0.05, value=0.5)
        self.btn_predict = QPushButton("Предсказать для неразмеченных")
        self.btn_predict_all = QPushButton("Для всех (кроме подтверждённых)")
        h = QHBoxLayout()
        h.addWidget(QLabel("Порог уверенности:"))
        h.addWidget(self.threshold)
        h.addWidget(self.btn_predict)
        h.addWidget(self.btn_predict_all)
        h.addStretch(1)
        v3.addLayout(h)

        # step 4: review
        c4, v4 = card("4. Проверка предложений")
        self.review = QTableWidget(0, 4)
        self.review.setHorizontalHeaderLabels(["Трек", "Исполнитель", "Настроение", "Уверенность"])
        self.review.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.review.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.review.horizontalHeader().setStretchLastSection(True)
        self.review.setColumnWidth(0, 260)
        self.review.setColumnWidth(1, 160)
        self.review.setColumnWidth(2, 120)
        self.accept_min = QDoubleSpinBox(minimum=0.1, maximum=0.99, singleStep=0.05, value=0.8)
        self.btn_accept = QPushButton("✓ Принять")
        self.btn_reject = QPushButton("✗ Отклонить")
        self.btn_accept_all = QPushButton("Принять все ≥")
        self.btn_clear = QPushButton("Сбросить все")
        self.btn_accept.setToolTip("Принять выбранные строки (клавиша A в библиотеке)")
        self.btn_reject.setToolTip("Отклонить выбранные строки: модель больше не предложит это настроение (клавиша D)")
        h = QHBoxLayout()
        for w in (self.btn_accept, self.btn_reject, self.btn_accept_all, self.accept_min, self.btn_clear):
            h.addWidget(w)
        h.addStretch(1)
        v4.addLayout(h)
        v4.addWidget(self.review, 1)

        # log
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumBlockCount(500)
        self.log.setFixedHeight(120)

        self.guide = QLabel(wordWrap=True)
        self.guide.setStyleSheet(f"background:{TOKENS['accent_soft'] if 'accent_soft' in TOKENS else TOKENS['surface2']};"
                                 f"border-left:3px solid {TOKENS['accent']}; padding:8px; font-size:13px;")
        self.btn_guide = QPushButton("Сделать")
        self.btn_auto = QPushButton("Авто ▶▶")
        self.btn_auto.setToolTip("Пройти все шаги подряд сами: анализ → CLAP → обучение → предсказание")
        self.chk_steps = QPushButton("Показать шаги вручную")
        self.chk_steps.setCheckable(True)
        self._manual_cards = [c1, cc, cw, c3]
        for c in self._manual_cards:
            c.setVisible(False)
        self._auto = False
        self.auto_chk = QCheckBox("Новые треки получают настроение автоматически")
        self.auto_chk.setChecked(ctx.settings.auto_tag)
        self.auto_thr = QComboBox()
        for key, label in moodcv.AUTO_MODE_LABELS.items():
            self.auto_thr.addItem(label, key)
        self.auto_thr.setCurrentIndex(max(0, self.auto_thr.findData(ctx.settings.auto_mode)))
        self.auto_thr.setToolTip("Строго: настроение ставится, только когда модель очень уверена (меньше треков, точнее). "
                                 "Широко: почти всем трекам, точность ниже. Остальное остаётся предложениями.")
        self.auto_chk.setText("Новые треки размечать автоматически")
        self.btn_auto_now = QPushButton("Разметить библиотеку сейчас")
        self.btn_auto_now.setToolTip("Определить настроение у всех треков без метки обученной моделью")
        self.auto_info = QLabel()
        self.auto_info.setStyleSheet(f"color:{TOKENS['text_muted']}")
        self.btn_uncertain = QPushButton("Что разметить следующим")
        self.btn_uncertain.setToolTip("Треки, в которых модель сомневается больше всего: их разметка полезнее всего")
        gh = QHBoxLayout()
        gh.addWidget(self.guide, 1)
        gh.addWidget(self.btn_guide)
        gh.addWidget(self.btn_auto)
        mid = QWidget()
        mv2 = QVBoxLayout(mid)
        mv2.addLayout(gh)
        mv2.addWidget(self.auto_info)
        mv2.addWidget(self.chk_steps, alignment=Qt.AlignmentFlag.AlignLeft)
        for c in (c1, cc, cw, c2, c3):
            mv2.addWidget(c)
        mv2.addWidget(self.log)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(c4)

        # ---- summary: how well the model is trained, which model, one button -------------------------------
        sc, sv = card("Модель настроений")
        self.sum_text = QLabel()
        self.sum_text.setTextFormat(Qt.TextFormat.RichText)
        self.sum_text.setWordWrap(True)
        self.model_box = QComboBox()
        self.model_box.setToolTip("Сохранённые версии модели: каждое обучение сохраняет прошлую версию. «Использовать» — "
                                  "библиотека переразмечается выбранной.\n«Дообучить» всегда учит НОВУЮ модель на всех ваших "
                                  "метках, выбранная на это не влияет.\nКачество разных моделей мерилось на разных наборах "
                                  "(сколько треков и настроений — в строке), поэтому сравнивать его напрямую нельзя.")
        self.btn_use_model = QPushButton("Использовать")
        self.btn_retrain = QPushButton("Дообучить на моих метках")
        self.btn_retrain.setObjectName("Primary")
        self.btn_retrain.setToolTip("Анализ звука новых размеченных треков → обучение (лучший вариант выбирается честной "
                                    "проверкой) → автоматическая разметка библиотеки. Ваши метки не меняются.")
        mh = QHBoxLayout()
        mh.addWidget(QLabel("Модель:"))
        mh.addWidget(self.model_box, 1)
        mh.addWidget(self.btn_use_model)
        mh.addSpacing(16)
        mh.addWidget(self.btn_retrain)
        mh.addWidget(self.btn_uncertain)
        self.btn_new_mood = QPushButton("＋ Настроение")
        self.btn_new_mood.setToolTip("Добавить своё настроение. Переименовать, объединить или удалить — "
                                     "«Расширенные настройки» → список настроений")
        mh.addWidget(self.btn_new_mood)
        self.quality = QTableWidget(0, 3)
        self.quality.setHorizontalHeaderLabels(["Настроение", "Примеров", "Качество"])
        self.quality.verticalHeader().setVisible(False)
        self.quality.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.quality.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.quality.horizontalHeader().setStretchLastSection(True)
        self.quality.setColumnWidth(0, 170)
        self.quality.setColumnWidth(1, 80)
        self.quality.setToolTip("Качество — честная проверка на исполнителях, которых модель не видела при обучении "
                                "(0.5 — угадывание, 1 — идеально). Меньше 10 примеров — модель по настроению слабая: "
                                "разметьте ещё треков (волна «Плохо размеченное»).")
        ah = QHBoxLayout()
        ah.addWidget(self.auto_chk)
        ah.addWidget(self.auto_thr)
        ah.addWidget(self.btn_auto_now)
        ah.addStretch(1)
        self.clap_short = QLabel()
        self.btn_clap_dir = QPushButton("Папка модели CLAP…")
        self.btn_clap_dir.setToolTip("Папка HuggingFace с весами laion/clap-htsat-fused (например CLAP_GUI\\workspace\\hf_cache)")
        ah.addWidget(self.clap_short)
        ah.addWidget(self.btn_clap_dir)
        self.btn_adv = QPushButton("Расширенные настройки ▸")
        self.btn_adv.setCheckable(True)
        self.btn_adv.setFlat(True)
        sv.addWidget(self.sum_text)
        sv.addLayout(mh)
        sv.addWidget(self.quality, 1)
        sv.addLayout(ah)
        sv.addWidget(self.btn_adv, alignment=Qt.AlignmentFlag.AlignLeft)
        split = QSplitter()
        for w in (left, mid, right):
            split.addWidget(w)
        split.setSizes([260, 560, 520])
        self.adv = split
        self.adv.setVisible(False)
        for c in self._manual_cards:                    # inside "advanced" every step is shown
            c.setVisible(True)
        self.chk_steps.hide()
        lay = QVBoxLayout(self)
        lay.addWidget(sc, 1)
        lay.addWidget(split, 3)
        self._chain: list[str] = []

        self.auto_chk.toggled.connect(self._auto_changed)
        self.auto_thr.currentIndexChanged.connect(self._auto_changed)
        self.btn_auto_now.clicked.connect(self._retag_all)
        self.btn_guide.clicked.connect(lambda: self.do_next_step())
        self.btn_auto.clicked.connect(self.run_auto)
        self.chk_steps.toggled.connect(self._toggle_manual)
        self.btn_uncertain.clicked.connect(self.show_uncertain)
        self.btn_adv.toggled.connect(self._toggle_adv)
        self.btn_retrain.clicked.connect(self.retrain)
        self.btn_new_mood.clicked.connect(lambda: self.add_mood())
        self.btn_use_model.clicked.connect(self.use_selected_model)
        self.btn_clap_dir.clicked.connect(self.choose_clap_dir)
        self.btn_add.clicked.connect(self.add_mood)
        self.btn_rename.clicked.connect(self.rename_mood)
        self.btn_del.clicked.connect(self.delete_mood)
        self.btn_an_labeled.clicked.connect(lambda: self.start_analyze("labeled"))
        self.btn_an_all.clicked.connect(lambda: self.start_analyze("all"))
        self.btn_train.clicked.connect(lambda: self.start_train())
        self.btn_web_lab.clicked.connect(lambda: self.start_web("labeled"))
        self.btn_web_all.clicked.connect(lambda: self.start_web("all"))
        self.btn_web_sug.clicked.connect(lambda: self.web_suggest())
        self.btn_web_agree.clicked.connect(lambda: self.web_agreement())
        self.btn_clap_lab.clicked.connect(lambda: self.start_clap("labeled"))
        self.btn_clap_all.clicked.connect(lambda: self.start_clap("all"))
        self.btn_zero.clicked.connect(lambda: self.start_zero_shot())
        self.btn_agree.clicked.connect(self.start_agreement)
        self.btn_predict.clicked.connect(lambda: self.start_predict(True))
        self.btn_predict_all.clicked.connect(lambda: self.start_predict(False))
        self.btn_accept.clicked.connect(lambda: self.resolve_selected(True))
        self.btn_reject.clicked.connect(lambda: self.resolve_selected(False))
        self.btn_accept_all.clicked.connect(self.accept_all)
        self.btn_clear.clicked.connect(self.clear_suggestions)
        self.review.cellDoubleClicked.connect(self._play_row)
        ctx.jobs.log.connect(lambda j, m: self.log.appendPlainText(m))
        ctx.jobs.metric.connect(self._on_metric)
        ctx.jobs.progress.connect(lambda j: self._enable())
        ctx.jobs.started.connect(lambda j: self._enable())
        ctx.jobs.finished.connect(self._on_finished)
        ctx.events.tracks_changed.connect(lambda ids: self.refresh_soon())
        ctx.events.library_changed.connect(self.refresh_soon)
        self._dirty = False
        self.refresh()

    # ---- state --------------------------------------------------------
    def refresh_soon(self) -> None:
        """A label changed somewhere: recompute when the page is shown (this page is heavy; labelling must stay fast)."""
        if self.isVisible():
            if not self._dirty:
                self._dirty = True
                QTimer.singleShot(400, self._refresh_if_dirty)
        else:
            self._dirty = True

    def _refresh_if_dirty(self) -> None:
        if self._dirty:
            self._dirty = False
            self.refresh()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._refresh_if_dirty()

    def refresh(self) -> None:
        db = self.ctx.db
        self.mood_list.clear()
        for m in db.list_moods():
            self.mood_list.addItem(f"{m['name']}   {m['n'] or 0} / {m['suggested'] or 0}")
        total = db.conn.execute("SELECT COUNT(*) FROM tracks WHERE missing=0").fetchone()[0]
        done = db.conn.execute("SELECT COUNT(*) FROM track_descriptors d JOIN tracks t ON t.id=d.track_id "
                               "WHERE t.missing=0").fetchone()[0]
        labelled = db.mood_training_set()
        lab_done = sum(1 for tid, _ in labelled if db.get_descriptors(tid))
        self.analysis_label.setText(f"Проанализировано {done} из {total} треков. "
                                    f"Размеченных: {len(labelled)}, из них с анализом: {lab_done}.")
        n_clap = db.conn.execute("SELECT COUNT(*) FROM track_embeddings e JOIN tracks t ON t.id=e.track_id "
                                 "WHERE e.model='clap' AND t.missing=0").fetchone()[0]
        lab_clap = sum(1 for tid, _ in labelled if db.get_embedding(tid, "clap"))
        if clap.available():
            self.clap_label.setText(f"CLAP найден. Эмбеддинги: {n_clap} из {total} треков (размеченных: {lab_clap} из "
                                    f"{len(labelled)}). Эмбеддинги также включают «Похожие треки» по звуку.")
        else:
            self.clap_label.setText(f"CLAP недоступен: {clap.unavailable_reason()}. Уже посчитанные эмбеддинги "
                                    f"({n_clap}) продолжают работать в обучении.")
        webtags._ensure(db)
        n_web = db.conn.execute("SELECT COUNT(*) FROM track_web").fetchone()[0]
        n_ab = db.conn.execute("SELECT COUNT(*) FROM track_web WHERE ab<>'{}' AND ab IS NOT NULL").fetchone()[0]
        import os
        key = "задан" if os.environ.get("MUSICPLAYER_LASTFM_KEY") else "не задан (необязательно: MUSICPLAYER_LASTFM_KEY, даёт теги вроде «sad», «chill»)"
        self.web_label.setText(f"Загружено для {n_web} треков, из них настроения AcousticBrainz есть у {n_ab}. "
                               f"Ключ Last.fm: {key}. Теги и настроения участвуют в обучении, только если улучшают "
                               f"честную оценку.")
        ids, X, Y, moods = mood.training_matrix(db, mode=mood.resolve_mode(db, "auto"))
        info = ", ".join(f"{m} ({int(Y[:, j].sum())})" for j, m in enumerate(moods)) or "нет"
        self.train_label.setText(f"Готово к обучению: {len(ids)} треков. Настроения (≥4 примеров): {info}")
        old = mood.load_model(self.ctx.models)
        if old and not self.last_train:
            self._show_info(old.info, old.moods)
        self._fill_review()
        from musicplayer.core import autotag
        n_auto = db.conn.execute("SELECT COUNT(DISTINCT track_id) FROM track_moods WHERE source='auto'").fetchone()[0]
        n_seed = db.conn.execute("SELECT COUNT(DISTINCT track_id) FROM track_moods WHERE source='seed'").fetchone()[0]
        n_user = db.conn.execute("SELECT COUNT(DISTINCT track_id) FROM track_moods WHERE source='manual'").fetchone()[0]
        self.auto_info.setText(f"Метки: ваши {n_user} · стартовые (Claude) {n_seed} · автоматические {n_auto} · "
                               f"ещё не определено {len(autotag.pending_ids(db))}")
        self.step = mood.next_step(db, self.ctx.models)
        self.guide.setText(f"<b>Следующий шаг:</b> {self.step['text']}")
        self._refresh_summary()
        self._enable()

    def _auto_changed(self, *_a) -> None:
        self.ctx.settings.auto_tag = self.auto_chk.isChecked()
        self.ctx.settings.auto_mode = self.auto_thr.currentData()
        self.ctx.save_settings()

    def _toggle_manual(self, on: bool) -> None:
        for c in self._manual_cards:
            c.setVisible(on)
        self.chk_steps.setText("Скрыть шаги вручную" if on else "Показать шаги вручную")

    JOB_STEPS = ("analyze_labeled", "clap_labeled", "train", "predict")

    def run_auto(self) -> None:
        """Do the recommended steps one after another until a step needs YOU (labelling, reviewing)."""
        self._auto = True
        self.do_next_step()
        if self.step["action"] not in self.JOB_STEPS:
            self._auto = False

    def do_next_step(self) -> None:
        """Run what the guide recommends (one click through the label -> analyse -> train -> predict loop)."""
        a = self.step["action"]
        if self.ctx.jobs.busy:
            return
        if a in ("library", "scan"):
            self.ctx.events.navigate.emit("library")
        elif a == "analyze_labeled":
            self.start_analyze("labeled")
        elif a == "clap_labeled":
            self.start_clap("labeled")
        elif a == "analyze_all":
            self.start_analyze("all")
        elif a == "train":
            self.start_train()
        elif a == "predict":
            self.start_predict(True)
        elif a == "review":
            self.review.setFocus()
            self.review.selectRow(0) if self.review.rowCount() else None
        elif a == "uncertain":
            self.show_uncertain()

    def show_uncertain(self, n: int = 40) -> list[dict]:
        rows = mood.uncertain_tracks(self.ctx.db, self.ctx.models, n)
        if not rows:
            self.log.appendPlainText("Нет проанализированных неразмеченных треков: сначала проанализируйте библиотеку.")
            return []
        self.ctx.events.show_rows.emit(rows, "Что разметить следующим")
        self.ctx.events.navigate.emit("library")
        return rows

    def _show_info(self, info: dict, moods: list[str]) -> None:
        val = info.get("val", {})
        per = "; ".join(f"{m}: {('%.2f' % v['val_auc']) if v.get('val_auc') is not None else '—'}"
                        for m, v in info.get("per_mood", {}).items())
        auc = val.get("auc")
        known = info.get("auc_random_split")
        self.metrics.setText(
            f"<b>{info.get('chosen', 'модель')}</b> · {info.get('tracks')} размеченных треков · признаки: {info.get('mode')}<br>"
            f"Честная оценка для <b>новых исполнителей</b>: AUC {('%.2f' % auc) if auc is not None else '—'}, верхнее "
            f"настроение угадано у {val.get('top1', 0) * 100:.0f}% треков."
            + (f"<br>Для исполнителей, у которых уже есть метки: AUC {known:.2f}." if known is not None else "")
            + (f"<br>Уверенность откалибрована (ошибка {info['ece_raw']:.2f} → {info['ece_calibrated']:.2f}): «80%» значит ~80%."
               if "ece_raw" in info else "")
            + (f"<br>Интернет-теги: {'использованы' if info.get('web_vocab') else 'не использованы'}, прирост честного AUC "
               f"{info['web_gain_auc']:+.3f}" if info.get("web_gain_auc") is not None else "")
            + f"<br>AUC по настроениям — {per}")

    def _fill_review(self) -> None:
        rows = []
        for r in self.ctx.db.conn.execute(
                "SELECT tm.track_id, m.name, tm.confidence, tm.source FROM track_moods tm JOIN moods m ON m.id=tm.mood_id "
                "WHERE tm.source NOT IN ('manual','seed') ORDER BY tm.confidence DESC LIMIT 2000"):
            rows.append(tuple(r))
        self.review.setRowCount(len(rows))
        for i, (tid, name, conf, src) in enumerate(rows):
            t = self.ctx.db.get_track(tid)
            vals = [t["title"], t["artist"], name, f"{int((conf or 0) * 100)}%  ({src})"]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.ItemDataRole.UserRole, (tid, name))
                self.review.setItem(i, j, it)
        self.btn_accept_all.setToolTip(f"Принять все предложения с уверенностью не ниже порога ({len(rows)} шт. в списке)")
        self.btn_clear.setToolTip("Удалить все предложения модели и Claude (подтверждённые метки не трогаются)")

    def _enable(self) -> None:
        busy = self.ctx.jobs.busy
        for b in (self.btn_an_labeled, self.btn_an_all, self.btn_train, self.btn_predict, self.btn_predict_all,
                  self.btn_guide):
            b.setEnabled(not busy)
        for b in (self.btn_web_lab, self.btn_web_all):
            b.setEnabled(not busy)
        for b in (self.btn_clap_lab, self.btn_clap_all, self.btn_zero, self.btn_agree):
            b.setEnabled(not busy and clap.available())


    # ---- summary ------------------------------------------------------
    def _refresh_summary(self) -> None:
        db = self.ctx.db
        m = mood.load_model(self.ctx.models)
        counts = db.mood_example_counts()
        if m is None:
            self.sum_text.setText("<b>Модель ещё не обучена.</b> Разметьте несколько треков и нажмите «Дообучить».")
        else:
            import time
            info = m.info or {}
            val = info.get("val", {})
            word, col = quality_word(val.get("auc"))
            mp = mood.model_path(self.ctx.models)
            when = time.strftime("%d.%m.%Y %H:%M", time.localtime(mp.stat().st_mtime)) if mp.exists() else "—"
            self.sum_text.setText(
                f"<b>Обучена {when}</b> на {info.get('tracks', '?')} ваших треках · качество "
                f"<b style='color:{col}'>{val.get('auc', 0):.2f} ({word})</b> · верхнее настроение угадывает у "
                f"<b>{val.get('top1', 0) * 100:.0f}%</b> треков"
                + (f"<br>С учётом жанра и других треков исполнителя: новые исполнители "
                   f"<b>{info['prior']['auc_new_after']:.2f}</b>, знакомые <b>{info['prior']['auc_known_after']:.2f}</b>"
                   if info.get("prior") else ""))
            self.sum_text.setToolTip(
                f"Алгоритм: {info.get('chosen')} · признаки: {info.get('mode')}\n"
                f"Проверка честная: модель оценивается на исполнителях, которых не видела при обучении.\n"
                + (f"Для уже знакомых исполнителей качество выше: {info['auc_random_split']:.2f}\n"
                   if info.get("auc_random_split") is not None else "")
                + (f"Уверенность откалибрована: «80%» значит примерно 80%." if "ece_raw" in info else ""))
        fresh = mood.labelled_since_training(db, m)
        if fresh:
            self.sum_text.setText(self.sum_text.text() + f"<br><span style='color:#D5A52C'>С последнего обучения вы разметили "
                                  f"ещё <b>{fresh}</b> трек{'а' if 2 <= fresh % 10 <= 4 and not 12 <= fresh % 100 <= 14 else '' if fresh % 10 == 1 and fresh % 100 != 11 else 'ов'}"
                                  f"{' — стоит дообучить' if fresh >= 10 else ''}.</span>")
        else:
            self.sum_text.setText(self.sum_text.text() + "<br><span style='color:#92928F'>Все ваши метки уже учтены моделью.</span>")
        waiting = mood.waiting_for_analysis(db, m)
        if waiting:
            self.sum_text.setText(self.sum_text.text() + f"<br><span style='color:#92928F'>Из них {waiting} ждут анализа звука — "
                                  f"«Дообучить» сделает его сам.</span>")
        self.btn_retrain.setText(f"Дообучить на моих метках (+{fresh})" if fresh else "Дообучить на моих метках")
        per = (m.info or {}).get("per_mood", {}) if m else {}
        names = [x["name"] for x in db.list_moods()]
        rows = sorted(names, key=lambda n: -((per.get(n) or {}).get("val_auc") or 0))
        self.quality.setRowCount(len(rows))
        for i, n in enumerate(rows):
            auc = (per.get(n) or {}).get("val_auc")
            k = counts.get(n, 0)
            word, col = quality_word(auc) if n in per else ("не обучено: мало примеров" if k < 4 else "нет в модели", "#8A9099")
            bar = "█" * int(round((max(auc, 0.5) - 0.5) * 20)) if auc is not None else ""
            cells = [n, str(k) + ("  ⚠" if k < 10 else ""), f"{bar}  {auc:.2f} {word}" if auc is not None else word]
            for j, v in enumerate(cells):
                it = QTableWidgetItem(v)
                if j == 2:
                    it.setForeground(QColor(col))
                if j == 1 and k < 10:
                    it.setToolTip("Мало примеров: нужно хотя бы 10–15 размеченных треков")
                self.quality.setItem(i, j, it)
        cur = self.model_box.currentData()
        self.model_box.blockSignals(True)
        self.model_box.clear()
        for label, p in saved_models(self.ctx.home):
            self.model_box.addItem(label, str(p))
        i = self.model_box.findData(cur)
        self.model_box.setCurrentIndex(max(i, 0))
        self.model_box.blockSignals(False)
        ok = clap.available()
        self.clap_short.setText("CLAP: найдена ✓" if ok else "CLAP: не найдена")
        self.clap_short.setToolTip("" if ok else clap.unavailable_reason())
        self.btn_retrain.setEnabled(not self.ctx.jobs.busy)

    def _toggle_adv(self, on: bool) -> None:
        self.adv.setVisible(on)
        self.btn_adv.setText("Расширенные настройки ▾" if on else "Расширенные настройки ▸")

    def retrain(self) -> None:
        """One button: analyse new labelled tracks -> train (honest choice of the variant) -> tag the library."""
        if self.ctx.jobs.busy:
            return
        self._chain = ["analyze", "clap", "train", "tag"]
        self._next_in_chain()

    def _next_in_chain(self) -> None:
        if not self._chain or self.ctx.jobs.busy:
            return
        step = self._chain.pop(0)
        db = self.ctx.db
        if step == "analyze":
            ids = [tid for tid, _ in db.mood_training_set() if not db.get_descriptors(tid)]
            if not ids:
                return self._next_in_chain()
            self.start_analyze("labeled", ids)
        elif step == "clap":                         # the model listens through CLAP too: new tracks need it
            ids = [tid for tid, _ in db.mood_training_set() if not db.get_embedding(tid, "clap")]
            if not ids or not clap.available():
                return self._next_in_chain()
            self.start_clap("labeled", ids)
        elif step == "train":
            self.start_train(features="auto", web=False)
        elif step == "tag":
            self._retag_all()

    def use_selected_model(self) -> None:
        import shutil
        from pathlib import Path
        src = Path(self.model_box.currentData() or "")
        dst = mood.model_path(self.ctx.models)
        if not src.exists() or src.resolve() == dst.resolve():
            return
        backup_model(self.ctx.models)
        shutil.copy2(src, dst)
        self.refresh()
        self._retag_all()                                # re-tag the library with the chosen model

    def _retag_all(self) -> None:
        """Automatic moods for every track without the user's labels (earlier automatic ones are replaced)."""
        win = self.window()
        if hasattr(win, "start_autotag") and not self.ctx.jobs.busy:
            win.start_autotag(self.ctx.db.unlabelled_ids())

    def choose_clap_dir(self) -> None:
        import os
        d = QFileDialog.getExistingDirectory(self, "Папка с моделью CLAP (HuggingFace hf_cache)")
        if not d:
            return
        os.environ["MUSICPLAYER_HF_HOME"] = d
        self.ctx.settings.clap_hf_home = d
        self.ctx.save_settings()
        if not clap.available():
            QMessageBox.warning(self, "CLAP", f"В этой папке модели нет: {clap.unavailable_reason()}")
        self.refresh()

    # ---- mood list ----------------------------------------------------
    def _selected_mood(self) -> str | None:
        it = self.mood_list.currentItem()
        return it.text().rsplit("   ", 1)[0] if it else None

    def add_mood(self, name: str | None = None) -> None:
        if not name:
            name, ok = QInputDialog.getText(self, "Новое настроение", "Название:")
            if not ok:
                return
        if name.strip():
            self.ctx.db.add_mood(name)
            self.ctx.events.library_changed.emit()

    def rename_mood(self) -> None:
        old = self._selected_mood()
        if old:
            new, ok = QInputDialog.getText(self, "Переименовать", "Новое название:", text=old)
            if ok and new.strip():
                self.ctx.db.rename_mood(old, new)
                self.ctx.events.library_changed.emit()

    def delete_mood(self) -> None:
        old = self._selected_mood()
        if old and QMessageBox.question(self, "Удалить настроение",
                                        f"Удалить «{old}» и все его метки?") == QMessageBox.StandardButton.Yes:
            self.ctx.db.delete_mood(old)
            self.ctx.events.library_changed.emit()

    # ---- jobs ---------------------------------------------------------
    def start_analyze(self, scope: str = "labeled", ids: list[int] | None = None) -> JobInfo:
        ctx = self.ctx
        if ids is None and scope == "labeled":
            ids = [tid for tid, _ in ctx.db.mood_training_set()]
        workers = self.workers.value()

        def fn(jc, emit):
            db = ctx.open_db()
            try:
                return mood.analyze_tracks(db, ids, jc, workers=workers)
            finally:
                db.close()
        return ctx.jobs.run("analyze", "Анализ звука", fn)

    def start_web(self, scope: str = "labeled", ids: list[int] | None = None) -> JobInfo:
        ctx = self.ctx
        if ids is None and scope == "labeled":
            ids = [tid for tid, _ in ctx.db.mood_training_set()]

        def fn(jc, emit):
            db = ctx.open_db()
            try:
                return webtags.fetch_tracks(db, ids, jc)
            finally:
                db.close()
        return ctx.jobs.run("web", "Теги из интернета", fn)

    def web_suggest(self, min_conf: float = 0.7) -> dict:
        r = webtags.suggest_from_web(self.ctx.db, None, min_conf)
        self.log.appendPlainText(f"Из интернета предложено настроений: {r['suggested']} (треков: {r['tracks']})")
        self.ctx.events.tracks_changed.emit([])
        self.ctx.events.library_changed.emit()
        return r

    def web_agreement(self) -> dict:
        r = webtags.agreement(self.ctx.db)
        if r["precision"] is None:
            self.log.appendPlainText("Сверка: нет размеченных треков с настроениями из интернета")
        else:
            self.log.appendPlainText(
                f"Сверка с вашими метками: сильные догадки интернета верны в {r['precision'] * 100:.0f}% "
                f"({r['correct']} из {r['strong_guesses']}); по настроениям: "
                + ", ".join(f"{m} {v['precision'] * 100:.0f}% ({v['guesses']})" for m, v in r["per_mood"].items()))
        return r

    def start_clap(self, scope: str = "labeled", ids: list[int] | None = None) -> JobInfo:
        ctx = self.ctx
        if ids is None and scope == "labeled":
            ids = [tid for tid, _ in ctx.db.mood_training_set()]

        def fn(jc, emit):
            db = ctx.open_db()
            try:
                return clap.embed_tracks(db, ids, jc)
            finally:
                db.close()
        return ctx.jobs.run("clap", "Эмбеддинги CLAP", fn)

    def start_zero_shot(self, only_unlabelled: bool = True, threshold: float = 0.3) -> JobInfo:
        ctx = self.ctx

        def fn(jc, emit):
            db = ctx.open_db()
            try:
                return clap.zero_shot(db, ctx.home, None, threshold, only_unlabelled, ctx=jc)
            finally:
                db.close()
        return ctx.jobs.run("zeroshot", "CLAP: предсказание без обучения", fn)

    def start_agreement(self) -> JobInfo:
        ctx = self.ctx

        def fn(jc, emit):
            db = ctx.open_db()
            try:
                r = clap.agreement(db, ctx.home)
                acc = r["accuracy"]
                jc.log("Сверка CLAP с вашими метками: " + (f"{acc * 100:.0f}% (случайно было бы {r['chance'] * 100:.0f}%); "
                       f"по настроениям: {r['per_mood_recall']}" if acc is not None else "нет размеченных треков с эмбеддингами"))
                return r
            finally:
                db.close()
        return ctx.jobs.run("agreement", "Сверка CLAP с метками", fn)

    def start_train(self, epochs: int | None = None, features: str | None = None, web=None) -> JobInfo:
        ctx = self.ctx
        feats = features or self.features.currentData()
        web_choice = self.web_mode.currentData() if web is None else web
        ep = epochs or self.epochs.value()
        self.chart.reset(ep)
        self.last_train = None

        return ctx.jobs.run_process("train", "Обучение модели настроений", "musicplayer.core.procjobs:train",
                                    {"db_path": str(ctx.db_path), "models": str(ctx.models), "epochs": ep,
                                     "features": feats, "web": web_choice})

    def start_predict(self, only_unlabelled: bool = True, threshold: float | None = None) -> JobInfo:
        ctx = self.ctx
        thr = threshold if threshold is not None else self.threshold.value()

        return ctx.jobs.run_process("predict", "Предсказание настроений", "musicplayer.core.procjobs:predict",
                                    {"db_path": str(ctx.db_path), "models": str(ctx.models), "threshold": thr,
                                     "only_unlabelled": only_unlabelled})

    def _on_metric(self, job: JobInfo, m: dict) -> None:
        if "train" in m:
            self.chart.add(m["train"])
        if "cv" in m:
            self.metrics.setText("<b>Сравнение моделей</b> (проверка по исполнителям, AUC / верхнее настроение):<br>"
                                 + "<br>".join(f"{r['name']}: {r['auc']:.3f} / {r['top1'] * 100:.0f}%" for r in m["cv"]))

    def _on_finished(self, job: JobInfo) -> None:
        if job.state == "failed":
            self.log.appendPlainText(f"Ошибка: {job.error}")
        elif job.state == "cancelled":
            self.log.appendPlainText("Отменено")
        elif job.kind == "train":
            self.last_train = job.result
            self._show_info(job.result, job.result["moods"])
        if job.kind in ("predict", "zeroshot", "clap", "web"):
            self.ctx.events.tracks_changed.emit([])
            self.ctx.events.library_changed.emit()
        self.refresh()
        if self._chain:
            if job.state == "finished":
                QTimer.singleShot(200, self._next_in_chain)
            else:
                self._chain = []
        if self._auto:                       # continue the chain after a successful step
            if job.state == "finished" and self.step["action"] in self.JOB_STEPS:
                QTimer.singleShot(200, self.do_next_step)
            else:
                self._auto = False

    # ---- review -------------------------------------------------------
    def _selected_pairs(self) -> list[tuple[int, str]]:
        rows = {i.row() for i in self.review.selectedIndexes()}
        return [self.review.item(r, 0).data(Qt.ItemDataRole.UserRole) for r in sorted(rows)]

    def resolve_selected(self, accept: bool) -> int:
        pairs = self._selected_pairs()
        for tid, name in pairs:
            self.ctx.db.resolve_suggestions([tid], accept=accept, moods=[name])
        self.ctx.events.tracks_changed.emit([t for t, _ in pairs])
        self.refresh()
        return len(pairs)

    def accept_all(self, min_conf: float | None = None) -> int:
        thr = min_conf if isinstance(min_conf, float) else self.accept_min.value()
        ids = [r[0] for r in self.ctx.db.conn.execute("SELECT DISTINCT track_id FROM track_moods WHERE source NOT IN ('manual','seed')")]
        n = self.ctx.db.resolve_suggestions(ids, accept=True, min_confidence=thr)
        self.ctx.events.tracks_changed.emit(ids)
        self.ctx.events.library_changed.emit()
        return n

    def clear_suggestions(self) -> None:
        self.ctx.db.clear_suggestions()
        self.ctx.events.tracks_changed.emit([])
        self.refresh()

    def _play_row(self, row: int, _col: int) -> None:
        tid, _ = self.review.item(row, 0).data(Qt.ItemDataRole.UserRole)
        t = self.ctx.db.get_track(tid)
        if t:
            self.ctx.player.play_tracks([t])
