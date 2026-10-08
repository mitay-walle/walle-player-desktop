"""Mood model: Freesound-style descriptors -> small MLP trained on the user's confirmed labels.

Pipeline (each step reports through a :class:`JobContext`, so the GUI/MCP can show it live):

1. :func:`analyze_tracks`  - descriptors for tracks that have none (process pool, stored in the DB);
2. :func:`train`           - MLP (multi-label, one sigmoid per mood) trained epoch by epoch on confirmed
                             moods, hold-out metrics every epoch, model saved to the data folder;
3. :func:`predict`         - probabilities for chosen tracks stored as *suggestions* (never as confirmed).
"""

from __future__ import annotations

import concurrent.futures as cf
import logging
import os
import pickle
import time
from dataclasses import dataclass, field
from pathlib import Path

import warnings

import numpy as np

warnings.filterwarnings("ignore", category=RuntimeWarning)  # all-NaN descriptor columns are expected

from .db import Database
from .descriptors import NUMERIC_DESCRIPTORS, TIMBRAL_DESCRIPTORS, descriptor_vector, extract_descriptors
from . import moodcv, webtags
from .jobs import CancelledError, JobContext, null_context

log = logging.getLogger(__name__)

try:  # numba's compiled-function cache must be writable and shared (frozen app dirs are not); inherited by workers
    from .paths import data_dir as _data_dir
    os.environ.setdefault("NUMBA_CACHE_DIR", str(_data_dir() / "numba_cache"))
except Exception:
    pass

SKIP = set(TIMBRAL_DESCRIPTORS) | {"single_event", "loopable", "start_time", "note_midi", "onset_count", "beat_count"}
FEATURES: tuple[str, ...] = tuple(n for n in NUMERIC_DESCRIPTORS if n not in SKIP)
FEATURE_NAMES = FEATURES + ("mode_minor",)
DESC_VERSION = 1


def feature_vector(desc: dict) -> np.ndarray:
    v = descriptor_vector(desc, FEATURES)
    ton = str(desc.get("tonality") or "").lower()
    mode = np.array([1.0 if "minor" in ton else 0.0 if "major" in ton else np.nan])
    return np.concatenate([v, mode])


# ---- descriptor features, version 2 (prepared like the Essentia / Freesound literature recommends) ----------------
# dropped: near-constant (pitch_min) and near-duplicates (|r| > 0.95 on this library: temporal_centroid_ratio ~
# temporal_centroid, spectral_energy ~ loudness, hpcp_entropy ~ hpcp_crest, note_confidence ~ pitch_confidence)
DROP_V2 = {"pitch_min", "temporal_centroid_ratio", "spectral_energy", "hpcp_entropy", "note_confidence"}
FEATURES_V2: tuple[str, ...] = tuple(n for n in FEATURES if n not in DROP_V2 and n != "bpm")
_KEYS = {"c": 0, "c#": 1, "db": 1, "d": 2, "d#": 3, "eb": 3, "e": 4, "f": 5, "f#": 6, "gb": 6, "g": 7, "g#": 8, "ab": 8,
         "a": 9, "a#": 10, "bb": 10, "b": 11}
FEATURE_NAMES_V2 = FEATURES_V2 + ("bpm_log_folded", "key_sin", "key_cos", "mode_minor")


def feature_vector_v2(desc: dict) -> np.ndarray:
    """v2: BPM folded into 70-140 (detectors often halve / double it) and log-scaled; the key on the circle of fifths
    (sin / cos) instead of nothing; major / minor; duplicated and constant descriptors left out."""
    v = descriptor_vector(desc, FEATURES_V2)
    bpm = desc.get("bpm")
    if isinstance(bpm, (int, float)) and bpm > 0:
        b = float(bpm)
        while b >= 140:
            b /= 2
        while b < 70:
            b *= 2
        bpm_f = np.log(b)
    else:
        bpm_f = np.nan
    ton = str(desc.get("tonality") or "").lower().split()
    root = _KEYS.get(ton[0]) if ton else None
    if root is not None:
        fifths = (root * 7) % 12
        ks, kc = np.sin(2 * np.pi * fifths / 12), np.cos(2 * np.pi * fifths / 12)
    else:
        ks = kc = np.nan
    minor = 1.0 if "minor" in ton else 0.0 if "major" in ton else np.nan
    return np.concatenate([v, [bpm_f, ks, kc, minor]])


FEAT_V = 2          # the version new models are trained with; a model remembers its own in info["feat_v"]


MODES = ("descriptors", "clap", "both")


def row_for(db: Database, tid: int, mode: str = "descriptors", vocab: list[str] | None = None,
            feat_v: int = 1) -> np.ndarray | None:
    """Feature row of a track for a feature mode (None when a needed analysis is missing).

    descriptors = Freesound-style audio descriptors; clap = 512-d CLAP embedding; both = concatenation."""
    parts = []
    if mode in ("descriptors", "both"):
        d = db.get_descriptors(tid)
        if not d:
            return None
        parts.append(feature_vector_v2(d) if feat_v >= 2 else feature_vector(d))
    if mode in ("clap", "both"):
        b = db.get_embedding(tid, "clap")
        if not b:
            return None
        parts.append(np.frombuffer(b, dtype=np.float32).astype(np.float64))
    if vocab is not None:                      # internet tags / AcousticBrainz moods (zeros + has_data=0 when unknown)
        parts.append(webtags.web_vector(webtags.get_web(db, tid), vocab))
    return np.concatenate(parts)


def resolve_mode(db: Database, mode: str = "auto") -> str:
    """'auto': descriptors + CLAP when most labelled tracks have both, else whatever is available."""
    if mode in MODES:
        return mode
    lab = [tid for tid, _ in db.mood_training_set()]
    d = sum(1 for t in lab if db.get_descriptors(t))
    c = sum(1 for t in lab if db.get_embedding(t, "clap"))
    both = sum(1 for t in lab if db.get_descriptors(t) and db.get_embedding(t, "clap"))
    if lab and both >= 0.8 * len(lab):
        # library-wide coverage decides: if CLAP embeddings exist for far more tracks than descriptors, a CLAP-only
        # model can predict many more tracks than a "both" model (which needs both analyses for every track)
        n_desc = db.conn.execute("SELECT COUNT(*) FROM track_descriptors").fetchone()[0]
        n_clap = db.conn.execute("SELECT COUNT(*) FROM track_embeddings WHERE model='clap'").fetchone()[0]
        return "clap" if n_clap >= 1.5 * max(n_desc, 1) else "both"
    if lab and c >= 0.8 * len(lab) and c > d:
        return "clap"
    return "descriptors"


# ---------------------------------------------------------------- analysis
_fault_file = None


def _enable_fault_log() -> None:
    """Native crashes in worker processes leave no Python traceback; write them to worker_fault.log."""
    global _fault_file
    if _fault_file is None:
        try:
            import faulthandler

            from .paths import data_dir
            _fault_file = open(data_dir() / "worker_fault.log", "a", buffering=1)
            faulthandler.enable(_fault_file, all_threads=True)
        except Exception:
            pass


def _analyze_one(args: tuple[int, str, float]) -> tuple[int, dict | None, str]:
    tid, path, seconds = args
    _enable_fault_log()
    try:
        return tid, extract_descriptors(path, max_seconds=seconds, timbral=False), ""
    except Exception as e:  # unreadable file: report, keep going
        return tid, None, str(e)


def analyze_tracks(db: Database, ids: list[int] | None = None, ctx: JobContext | None = None,
                   workers: int = 4, seconds: float = 30.0) -> dict:
    """Compute descriptors for ``ids`` (default: every track without them) and store them."""
    ctx = ctx or null_context()
    todo = [r for r in db.tracks_without_descriptors()]
    if ids is not None:
        want = set(ids)
        todo = [r for r in todo if r["id"] in want]
    total, done, failed = len(todo), 0, []
    ctx.log(f"Анализ звука: {total} треков, процессов: {workers}")
    ctx.progress(0, total, "start")
    if not total:
        return {"analyzed": 0, "failed": []}
    jobs = [(r["id"], r["path"], seconds) for r in todo]
    t0 = time.time()
    finished: set[int] = set()

    def record(tid: int, desc: dict | None, err: str) -> None:
        nonlocal done
        finished.add(tid)
        if desc is None:
            failed.append((tid, err))
        else:
            db.set_descriptors(tid, desc, DESC_VERSION)
        done += 1
        eta = (time.time() - t0) / done * (total - done)
        ctx.progress(done, total, f"{done}/{total}, осталось ~{int(eta)} с")

    def isolated(job: tuple) -> None:
        """One track in a throw-away process: a native crash costs only this track."""
        try:
            with cf.ProcessPoolExecutor(max_workers=1) as one:
                record(*one.submit(_analyze_one, job).result())
        except cf.process.BrokenProcessPool:
            record(job[0], None, "crash in the analysis process (numba/librosa)")

    try:
        with cf.ProcessPoolExecutor(max_workers=workers) as ex:
            # warm-up: the first track alone compiles/caches numba code, so workers do not race on the cache
            record(*ex.submit(_analyze_one, jobs[0]).result())
            futs = [ex.submit(_analyze_one, j) for j in jobs[1:]]
            try:
                for f in cf.as_completed(futs):
                    ctx.check()
                    record(*f.result())
            except CancelledError:
                for f in futs:
                    f.cancel()
                raise
    except cf.process.BrokenProcessPool:
        ctx.log("Процесс анализа аварийно завершился, дочитываю оставшиеся по одному")
        for j in jobs:
            if j[0] not in finished:
                ctx.check()
                isolated(j)
    ctx.log(f"Анализ завершён: {done - len(failed)} ok, {len(failed)} ошибок, {time.time() - t0:.0f} с")
    return {"analyzed": done - len(failed), "failed": failed}


# ---------------------------------------------------------------- model
@dataclass
class MoodModel:
    moods: list[str]
    mean: np.ndarray
    std: np.ndarray
    net: object
    info: dict = field(default_factory=dict)
    platt: list | None = None        # per-mood (a, b): calibrated p = sigmoid(a*logit(p)+b)

    def transform(self, X: np.ndarray) -> np.ndarray:
        z = (X - self.mean) / self.std
        return np.nan_to_num(z, nan=0.0, posinf=0.0, neginf=0.0)

    def predict_proba(self, X: np.ndarray, calibrated: bool = True) -> np.ndarray:
        P = np.asarray(self.net.predict_proba(self.transform(X)))
        platt = getattr(self, "platt", None)        # models saved before calibration existed have none
        return moodcv.apply_platt(P, platt) if calibrated and platt else P


def model_path(models_dir: str | Path) -> Path:
    return Path(models_dir) / "mood_model.pkl"


def save_model(m: MoodModel, models_dir: str | Path) -> Path:
    p = model_path(models_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        pickle.dump(m, f)
    return p


def load_model(models_dir: str | Path) -> MoodModel | None:
    p = model_path(models_dir)
    if not p.exists():
        return None
    with open(p, "rb") as f:
        return pickle.load(f)


def model_row(db: Database, model: "MoodModel", tid: int) -> np.ndarray | None:
    """The feature row of a track exactly as this model was trained (mode, web vocabulary, descriptor version)."""
    info = model.info or {}
    return row_for(db, tid, info.get("mode", "descriptors"), info.get("web_vocab"), info.get("feat_v", 1))


def track_probs(db: Database, model: "MoodModel", tid: int) -> dict[str, float]:
    """{mood: calibrated probability} for EVERY mood of the model (empty when the track has no audio analysis)."""
    from .db import canonical_mood
    row = model_row(db, model, tid)
    if row is None:
        return {}
    from . import moodprior
    p = moodprior.apply(db, model, [tid], model.predict_proba(row.reshape(1, -1)))[0]
    out: dict[str, float] = {}
    for j, m in enumerate(model.moods):
        name = canonical_mood(m)
        if name:
            out[name] = max(out.get(name, 0.0), float(p[j]))
    return out


def track_guesses(db: Database, model: "MoodModel", tid: int, min_prob: float = 0.3, max_n: int = 4,
                  second_min: float = 0.2) -> dict[str, float]:
    """{mood: calibrated probability} the model believes in for one track: every mood >= min_prob (at most max_n),
    at least the two best ones when the second reaches ``second_min``.  Rejected moods are left out."""
    row = model_row(db, model, tid)
    if row is None:
        return {}
    from . import moodprior
    p = moodprior.apply(db, model, [tid], model.predict_proba(row.reshape(1, -1)))[0]
    no = db.mood_negatives().get(tid, set())
    order = [j for j in np.argsort(-p) if model.moods[j] not in no]
    pick = [j for j in order if p[j] >= min_prob][:max_n] or order[:1]
    if len(pick) < 2 and len(order) > 1 and p[order[1]] >= second_min:
        pick = order[:2]
    from .db import canonical_mood
    out = {}
    for j in pick:
        name = canonical_mood(model.moods[j])
        if name:
            out[name] = float(p[j])
    return out


def labelled_since_training(db: Database, model: "MoodModel | None") -> int:
    """Tracks with your mood labels that the current model has not learned from yet (and could: live / demo
    recordings are never learned from, see training_matrix)."""
    from .flags import POOR_SOUND
    poor = {r[0] for r in db.conn.execute(
        f"SELECT DISTINCT track_id FROM track_flags WHERE flag IN ({','.join('?' * len(POOR_SOUND))})", POOR_SOUND)}
    manual = {r[0] for r in db.conn.execute("SELECT DISTINCT track_id FROM track_moods WHERE source='manual'")}
    labelled = {t for t, _ in db.mood_training_set() if t not in poor or t in manual}
    if model is None:
        return len(labelled)
    seen = set((model.info or {}).get("train_ids") or [])
    if seen:
        return len(labelled - seen)
    return max(0, len(labelled) - int((model.info or {}).get("tracks") or 0))    # older models: an estimate


def waiting_for_analysis(db: Database, model: "MoodModel | None") -> int:
    """Labelled tracks the model cannot learn from yet because their sound is not analysed (descriptors / CLAP)."""
    mode = (model.info or {}).get("mode", "both") if model else "both"
    return sum(1 for t, _ in db.mood_training_set() if row_for(db, t, mode) is None)


def training_matrix(db: Database, min_per_mood: int = 4,
                    mode: str = "descriptors", feat_v: int = 1) -> tuple[list[int], np.ndarray, np.ndarray, list[str]]:
    """Confirmed, analysed tracks -> (ids, X, Y indicator, moods with enough examples)."""
    from .flags import POOR_SOUND
    poor = {r[0] for r in db.conn.execute(
        f"SELECT DISTINCT track_id FROM track_flags WHERE flag IN ({','.join('?' * len(POOR_SOUND))})", POOR_SOUND)}
    manual = {r[0] for r in db.conn.execute("SELECT DISTINCT track_id FROM track_moods WHERE source='manual'")}
    # live / demo recordings sound poor and mislead audio analysis: not used for training (unless hand-labelled)
    labelled = [(t, m) for t, m in db.mood_training_set() if t not in poor or t in manual]
    counts: dict[str, int] = {}
    for tid, ms in labelled:
        if row_for(db, tid, mode, feat_v=feat_v) is not None:
            for m in ms:
                counts[m] = counts.get(m, 0) + 1
    moods = sorted(m for m, n in counts.items() if n >= min_per_mood)
    negatives = db.mood_negatives()
    have = {tid for tid, _ in labelled}
    # tracks the user rejected a suggestion for are informative negatives even without any positive label
    labelled = labelled + [(tid, []) for tid, neg in negatives.items()
                           if tid not in have and any(m in moods for m in neg)]
    ids, rows, ys = [], [], []
    for tid, ms in labelled:
        r = row_for(db, tid, mode, feat_v=feat_v)
        if r is None or not (any(m in moods for m in ms) or negatives.get(tid)):
            continue
        ids.append(tid)
        rows.append(r)
        ys.append([1 if m in ms else 0 for m in moods])
    X = np.vstack(rows) if rows else np.zeros((0, 1))
    return ids, X, np.array(ys, dtype=int).reshape(len(ids), len(moods)), moods


def _artist_key(db: Database, tid: int) -> str:
    t = db.get_track(tid) or {}
    return (t.get("albumartist") or t.get("artist") or str(tid)).strip().casefold()


def train(db: Database, models_dir: str | Path, ctx: JobContext | None = None, epochs: int = 120,
          hidden: int = 48, min_per_mood: int = 4, val_fraction: float = 0.25, seed: int = 0,
          on_epoch=None, features: str = "auto", models: tuple[str, ...] = ("logreg", "mlp"), on_cv=None,
          web: bool | str = "auto", feat_v: int = FEAT_V) -> dict:
    """Train on confirmed labels with honest evaluation (see :mod:`moodcv`).

    1. Grouped-by-artist cross-validation compares logistic regression (several C) and an MLP.
    2. The best candidate is refitted on all labels; Platt calibration comes from its out-of-fold predictions.
    ``on_epoch(dict)`` receives the live learning curve of the MLP (when it wins), ``on_cv(list)`` the leaderboard."""
    ctx = ctx or null_context()
    t0 = time.time()
    mode = resolve_mode(db, features)
    ids, X, Y, moods = training_matrix(db, min_per_mood, mode, feat_v)
    ctx.log(f"Признаки: {mode}")
    if len(ids) < 8 or not moods:
        raise ValueError(f"Мало размеченных треков с анализом звука: {len(ids)} "
                         f"(нужно ≥8, у каждого настроения ≥{min_per_mood})")
    groups = [_artist_key(db, i) for i in ids]
    folds, grouped = moodcv.make_folds(groups, 5, seed)
    ctx.log(f"Проверка: {len(folds)} блоков, " + (f"группировка по исполнителям ({len(set(groups))})" if grouped
                                                    else "исполнителей мало — случайные блоки"))

    def make_std(Xm: np.ndarray):
        def standardiser(tr: np.ndarray):
            mean = np.nanmean(Xm[tr], axis=0)
            std = np.nanstd(Xm[tr], axis=0)
            mean = np.where(np.isfinite(mean), mean, 0.0)
            std = np.where(np.isfinite(std) & (std > 1e-9), std, 1.0)
            return lambda idx: np.nan_to_num((Xm[idx] - mean) / std, nan=0.0, posinf=0.0, neginf=0.0)
        return standardiser

    def run_cv(Xm: np.ndarray, label: str):
        std_fn = make_std(Xm)
        cands = moodcv.default_candidates(models, epochs, hidden, (len(FEATURE_NAMES_V2) if feat_v >= 2 else len(FEATURE_NAMES)) if mode == "both" else None)
        oof: dict[str, np.ndarray] = {}
        board = []
        for k, cand in enumerate(cands):
            ctx.check()
            ctx.progress(k, len(cands), f"{label}: {cand.name}")
            P = moodcv.oof_predictions(cand, std_fn, Y, folds)
            oof[cand.name] = P
            cand.metrics = moodcv.macro_metrics(Y, P)
            board.append({"name": f"{cand.name}" + (" + интернет-теги" if label.endswith("интернет") else ""), **cand.metrics})
            ctx.log(f"  [{label}] {cand.name}: AUC {cand.metrics['auc']:.3f}, F1 {cand.metrics['f1']:.3f}, "
                    f"top-1 {cand.metrics['top1']:.3f}")
        best = max(cands, key=lambda c: (round(c.metrics["auc"], 4), c.metrics["top1"]))
        return {"cands": cands, "oof": oof, "board": board, "best": best, "std": std_fn}

    runs = [("база", X, None)]
    vocab = None
    if web:
        webs = {i: webtags.get_web(db, i) for i in ids}
        n_web = sum(1 for w in webs.values() if w and (w["tags"] or w["ab"]))
        if web is True or n_web >= max(8, 0.3 * len(ids)):
            vocab = webtags.top_tags(db, 30)
            W = np.vstack([webtags.web_vector(webs[i], vocab) for i in ids])
            runs.append(("интернет", np.hstack([X, W]), vocab))
            ctx.log(f"Интернет-данные есть у {n_web} из {len(ids)} треков: проверю, помогают ли они")
    results = []
    for label, Xm, voc in runs:
        res = run_cv(Xm, label)
        res.update(label=label, X=Xm, vocab=voc)
        results.append(res)
        if on_cv:
            on_cv(sorted([r for x in results for r in x["board"]], key=lambda r: -r["auc"]))
    chosen = results[0]
    # the extra feature block must beat the base by more than the noise of a small cross-validation
    margin = 0.01 + 0.4 / max(len(set(groups)), 1) ** 0.5
    if len(results) > 1 and results[1]["best"].metrics["auc"] > chosen["best"].metrics["auc"] + margin:
        chosen = results[1]
    ctx.log("Признаки: " + ("с интернет-тегами (улучшают честную оценку)" if chosen["label"] == "интернет" else
                            "без интернет-тегов" + (f" (прирост меньше порога {margin:.3f})" if len(results) > 1 else "")))
    X, vocab, cands, oof, best = chosen["X"], chosen["vocab"], chosen["cands"], chosen["oof"], chosen["best"]
    standardiser = chosen["std"]
    web_gain = (results[1]["best"].metrics["auc"] - results[0]["best"].metrics["auc"]) if len(results) > 1 else None
    ctx.log(f"Лучшая: {best.name}")

    # learning curve of the MLP on the first fold (live chart); only meaningful when the MLP is the winner
    history: list[dict] = []
    if best.name == "нейросеть MLP":
        from sklearn.neural_network import MLPClassifier
        tr, va = folds[0]
        tf = standardiser(tr)
        net = MLPClassifier(hidden_layer_sizes=(hidden,), alpha=1e-2, learning_rate_init=3e-3, random_state=seed,
                            batch_size=min(32, len(tr)))
        for ep in range(1, epochs + 1):
            ctx.check()
            net.partial_fit(tf(tr), Y[tr], classes=np.arange(len(moods))) if ep == 1 else net.partial_fit(tf(tr), Y[tr])
            m = moodcv.macro_metrics(Y[va], np.asarray(net.predict_proba(tf(va))))
            row = {"epoch": ep, "loss": float(net.loss_), "val_f1": m["f1"], "val_auc": m["auc"]}
            history.append(row)
            if on_epoch:
                on_epoch(row)
            ctx.progress(ep, epochs, f"кривая обучения: эпоха {ep}/{epochs}, loss {net.loss_:.3f}")

    # how much a plain random split would have flattered the score (artist leakage)
    rnd_folds, _ = moodcv.make_folds([str(i) for i in range(len(ids))], 5, seed)
    P_rnd = moodcv.oof_predictions(best, standardiser, Y, rnd_folds)
    auc_random = moodcv.macro_metrics(Y, P_rnd)["auc"]

    allidx = np.arange(len(ids))
    tf = standardiser(allidx)
    est = best.make().fit(tf(allidx), Y)
    P = oof[best.name]
    platt = moodcv.fit_platt(Y, P)
    Pcal = moodcv.apply_platt(P, platt)
    ece_raw, ece_cal = moodcv.expected_calibration_error(Y, P), moodcv.expected_calibration_error(Y, Pcal)
    # same-genre / same-artist tracks share moods: fit how much to lean on them (honest, see moodprior)
    from . import moodprior
    prior = moodprior.fit_weights(db, moods, ids, Y, Pcal, moodcv.apply_platt(P_rnd, platt))
    ctx.log(f"Соседи по жанру и исполнителю: вес жанра {prior['genre']}, исполнителя {prior['artist']} · "
            f"новые исполнители AUC {prior['auc_new_before']:.3f} → {prior['auc_new_after']:.3f}, "
            f"знакомые {prior['auc_known_before']:.3f} → {prior['auc_known_after']:.3f}")
    if prior["genre"]:
        Pcal = moodprior.blend(Pcal, ids, moodprior.Neighbours(db, moods), prior["genre"], 0.0, new_artist=True)
    auto_thr = {k: moodcv.auto_thresholds(Y, Pcal, moods, k) for k in moodcv.AUTO_PRESETS}
    mean = np.nanmean(X, axis=0)
    std = np.nanstd(X, axis=0)
    mean, std = np.where(np.isfinite(mean), mean, 0.0), np.where(np.isfinite(std) & (std > 1e-9), std, 1.0)
    per = {}
    from sklearn.metrics import roc_auc_score
    for j, name in enumerate(moods):
        per[name] = {"examples": int(Y[:, j].sum()),
                     "val_auc": float(roc_auc_score(Y[:, j], P[:, j])) if 0 < Y[:, j].sum() < len(Y) else None}
    model = MoodModel(moods, mean, std, est, platt=platt)
    model.info = {"trained_at": time.time(), "tracks": len(ids), "epochs": epochs, "val": best.metrics,
                  "per_mood": per, "seconds": time.time() - t0, "mode": mode, "n_features": int(X.shape[1]),
                  "chosen": best.name, "cv": {c.name: c.metrics for c in cands}, "grouped": grouped,
                  "n_groups": len(set(groups)), "auc_random_split": float(auc_random),
                  "web_vocab": vocab, "web_gain_auc": web_gain, "auto_thresholds": auto_thr,
                  "ece_raw": ece_raw, "ece_calibrated": ece_cal, "prior": prior, "train_ids": [int(i) for i in ids], "feat_v": feat_v}
    save_model(model, models_dir)
    ctx.log(f"Готово за {model.info['seconds']:.1f} с. Честная оценка (по исполнителям): AUC {best.metrics['auc']:.3f}, "
            f"верхнее настроение верно у {best.metrics['top1'] * 100:.0f}% треков (для НОВЫХ исполнителей). Для исполнителей, "
            f"треки которых уже размечены, AUC выше: {auc_random:.3f}. Калибровка: ошибка {ece_raw:.3f} → {ece_cal:.3f}")
    return {**model.info, "history": history, "moods": moods}


def predict(db: Database, models_dir: str | Path, ids: list[int] | None = None, threshold: float = 0.5,
            ctx: JobContext | None = None, only_unlabelled: bool = True, top_k: int = 3,
            auto_threshold: float | None = None, auto_mode: str | None = None) -> dict:
    """Store model predictions as suggestions.  Tracks need descriptors (run :func:`analyze_tracks` first)."""
    ctx = ctx or null_context()
    model = load_model(models_dir)
    if model is None:
        raise ValueError("Модель ещё не обучена")
    if ids is None:
        ids = db.unlabelled_ids() if only_unlabelled else [t["id"] for t in db.search(limit=10**9)]
    rows = [r for r in (db.get_track(i) for i in ids) if r]
    mode = model.info.get("mode", "descriptors")
    vocab = model.info.get("web_vocab")
    fv = (model.info or {}).get("feat_v", 1)
    todo = [(r["id"], row_for(db, r["id"], mode, vocab, fv)) for r in rows]
    todo = [(i, d) for i, d in todo if d is not None]
    skipped = len(rows) - len(todo)
    items, n = [], 0
    ctx.progress(0, len(todo), "predict")
    if todo:
        from . import moodprior
        P = moodprior.apply(db, model, [i for i, _ in todo], model.predict_proba(np.vstack([d for _, d in todo])))
        for (tid, _), p in zip(todo, P):
            n += 1
            best = np.argsort(-p)[:top_k]
            chosen = [j for j in best if p[j] >= threshold] or [int(best[0])]  # always give the best guess
            db.conn.execute("DELETE FROM track_moods WHERE track_id=? AND source IN ('model','auto')", (tid,))
            items += [(tid, model.moods[j], float(p[j])) for j in chosen]
            ctx.progress(n, len(todo), f"{n}/{len(todo)}")
        if auto_threshold is None and auto_mode is None:
            db.suggest_moods(items, "model")
        else:
            if auto_threshold is None:
                auto_threshold = 0.0
            # A mood becomes the track's own ("auto") only when the model is reliable FOR THAT MOOD: its probability
            # passes the mood's threshold learned from honest validation (high-prevalence moods need more, moods
            # the model cannot tell apart never qualify) - at most two per track.  The rest stay suggestions.
            per_mood = model.info.get("auto_thresholds") or {}
            if "balanced" in per_mood:                       # presets: strict / balanced / wide
                per_mood = per_mood.get(auto_mode or "balanced", per_mood["balanced"])
            auto_items, sug_items = [], []
            by_track: dict[int, list] = {}
            for it in items:
                by_track.setdefault(it[0], []).append(it)
            for tid, cand in by_track.items():
                ok = []
                for it in cand:
                    t = per_mood.get(it[1], auto_threshold) if per_mood else auto_threshold
                    if t is not None and it[2] >= max(t, auto_threshold if not per_mood else 0.0):
                        ok.append((it[2] - t, it))
                ok.sort(key=lambda x: -x[0])
                chosen_auto = [it for _, it in ok[:2]]
                auto_items += chosen_auto
                sug_items += [it for it in cand if it not in chosen_auto]
            db.suggest_moods(auto_items, "auto")
            db.suggest_moods(sug_items, "model")
        for tid, _ in todo:
            db.refresh_fts(tid)
    ctx.log(f"Предсказано {len(todo)} треков (признаки: {mode}), без анализа пропущено {skipped}")
    return {"predicted": len(todo), "suggestions": len(items), "skipped_no_descriptors": skipped, "features": mode}


def uncertain_tracks(db: Database, models_dir: str | Path, n: int = 20) -> list[dict]:
    """Active learning: analysed tracks without a confirmed mood on which the model is least sure.

    Labelling these helps most.  Without a model, a random sample of analysed tracks is returned."""
    import random
    model = load_model(models_dir)
    mode = model.info.get("mode", "descriptors") if model else "descriptors"
    vocab = model.info.get("web_vocab") if model else None
    pool = [t for t in (db.get_track(i) for i in db.unlabelled_ids()) if t]
    fv = (model.info or {}).get("feat_v", 1) if model else 1
    rows_ = {r["id"]: row_for(db, r["id"], mode, vocab, fv) for r in pool}
    cands = [r for r in pool if rows_.get(r["id"]) is not None]
    if not cands:
        return []
    if model is None:
        random.shuffle(cands)
        return cands[:n]
    from . import moodprior
    P = moodprior.apply(db, model, [r["id"] for r in cands], model.predict_proba(np.vstack([rows_[r["id"]] for r in cands])))
    score = np.abs(P.max(axis=1) - 0.5)                 # best guess near 50% = most uncertain
    out = []
    for i in np.argsort(score)[:n]:
        r = dict(cands[i])
        r["uncertainty"] = round(float(1 - 2 * score[i]), 3)
        out.append(r)
    return out


def next_step(db: Database, models_dir: str | Path) -> dict:
    """What to do next in the label -> analyse -> train -> predict -> review loop (for the UI and Claude)."""
    c = db.conn
    labelled = db.mood_training_set()
    analysed_labelled = sum(1 for tid, _ in labelled if db.get_descriptors(tid))
    ids, X, Y, moods = training_matrix(db)
    model = load_model(models_dir)
    total = c.execute("SELECT COUNT(*) FROM tracks WHERE missing=0").fetchone()[0]
    analysed = c.execute("SELECT COUNT(*) FROM track_descriptors d JOIN tracks t ON t.id=d.track_id "
                         "WHERE t.missing=0").fetchone()[0]
    pending = c.execute("SELECT COUNT(*) FROM track_moods WHERE source NOT IN ('manual','seed','auto')").fetchone()[0]
    if total == 0:
        return {"step": "scan", "action": "scan",
                "text": "Библиотека пуста: выберите папку с музыкой и нажмите «Сканировать»."}
    if len(labelled) < 16 or len(moods) < 2 and analysed_labelled >= len(labelled):
        need = max(0, 16 - len(labelled))
        return {"step": "label", "action": "library",
                "text": f"Разметьте несколько треков настроениями (ещё около {need}; нужно минимум по 4 трека "
                        f"на каждое из двух настроений): выберите трек в библиотеке и нажмите кнопку настроения."}
    if analysed_labelled < len(labelled):
        return {"step": "analyze_labeled", "action": "analyze_labeled",
                "text": f"Проанализируйте звук размеченных треков (осталось {len(labelled) - analysed_labelled})."}
    from . import clap
    if clap.available():
        lab_clap = sum(1 for tid, _ in labelled if db.get_embedding(tid, "clap"))
        if lab_clap < len(labelled):
            return {"step": "clap_labeled", "action": "clap_labeled",
                    "text": f"Посчитайте эмбеддинги CLAP для размеченных треков (осталось {len(labelled) - lab_clap}): "
                            f"готовая модель делает обучение заметно точнее."}
    stale = model is None or len(ids) - int(model.info.get("tracks", 0)) >= 10
    if stale:
        why = ("Модель ещё не обучена." if model is None else
               f"С прошлого обучения добавилось {len(ids) - int(model.info.get('tracks', 0))} размеченных треков.")
        return {"step": "train", "action": "train", "text": why + " Обучите модель."}
    if pending:
        return {"step": "review", "action": "review",
                "text": f"Проверьте предложения модели ({pending}): ✓ принять или ✗ отклонить — это улучшает "
                        f"следующее обучение."}
    if analysed < total:
        return {"step": "analyze_all", "action": "analyze_all",
                "text": f"Проанализируйте остальную библиотеку ({total - analysed} треков, около 1 с на трек), "
                        f"чтобы модель могла предсказывать настроения для всех."}
    unl = c.execute("SELECT COUNT(*) FROM tracks t WHERE t.missing=0 AND NOT EXISTS(SELECT 1 FROM track_moods m "
                    "WHERE m.track_id=t.id)").fetchone()[0]
    if unl:
        return {"step": "predict", "action": "predict", "text": f"Предскажите настроения для {unl} неразмеченных треков."}
    return {"step": "done", "action": "uncertain",
            "text": "Все треки с настроениями. Можно уточнить спорные: «Что разметить следующим»."}
