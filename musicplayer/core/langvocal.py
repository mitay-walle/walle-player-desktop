"""Language of the lyrics and instrumental / vocals, for filters ("today Russian, tomorrow foreign, or instrumental").

* Language: the script of the title / album decides when it is unambiguous (Cyrillic -> ru, kana -> ja, hangul -> ko,
  han -> zh).  For Latin titles the ARTIST decides: a band whose other titles are Cyrillic sings Russian, otherwise
  English; a small override table fixes artists whose titles are Latin although they sing Russian.  (Real language
  identification from the audio needs a speech model such as Whisper - not installed, so this is a transparent heuristic
  that the user can correct per track or per artist.)
* Vocals: logistic regression on the CLAP embedding + audio descriptors, trained on labelled examples
  (instrumental vs. has vocals), validated grouped by artist.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from .db import Database

LANGS = {"ru": "русский", "en": "английский", "ja": "японский", "ko": "корейский", "zh": "китайский", "fr": "французский",
         "es": "испанский", "de": "немецкий", "other": "другой"}
_CYR = re.compile(r"[Ѐ-ӿ]")
_KANA = re.compile(r"[぀-ヿ]")
_HANGUL = re.compile(r"[가-힯]")
_HAN = re.compile(r"[一-鿿]")
_LAT = re.compile(r"[A-Za-z]")
_FOREIGN_WORDS = {"intro", "outro", "interlude", "instrumental", "ost", "theme"}

# Russian-language artists whose titles are written in Latin (extend freely; keys are casefolded names).
RU_ARTISTS = {"blinch", "2nd season", "jane air", "f.p.g", "f.p.g.", "anacondaz", "anakondaz", "noize mc", "cause i can",
              "want you now", "twix", "pyrokinesis", "kill the barber!", "double fault", "cmh", "zmey gorynich", "stervell",
              "rusted", "distemper", "dead blonde", "5 diez", "obba nebba", "puzzle monsters", "dew", "anyway", "7000$",
              "such a beautiful day", "louna", "lumen", "lampasy", "тараканы!", "cheese people", "mestis",
              "sergey golovin", "stary olsa"}
# artists whose lyrics are neither Russian nor English (casefolded name -> language code)
LANG_OVERRIDES = {"my first story": "ja", "ryou sakai project": "ja", "maximum the hormone": "ja", "hiroyuki sawano": "ja",
                  "unravel": "ja", "the rose": "ko", "notre dame de paris": "fr", "shakira": "es"}


def script_lang(text: str | None) -> str | None:
    t = text or ""
    if _KANA.search(t):
        return "ja"
    if _HANGUL.search(t):
        return "ko"
    if _HAN.search(t):
        return "zh"
    if _CYR.search(t):
        return "ru"
    return "lat" if _LAT.search(t) else None


def detect_languages(db: Database, overrides: dict[str, str] | None = None) -> dict[int, str]:
    """{track_id: language} for every track (does not write)."""
    over = {**{a: "ru" for a in RU_ARTISTS}, **LANG_OVERRIDES, **{k.casefold(): v for k, v in (overrides or {}).items()}}
    rows = db.conn.execute("SELECT id,title,album,artist,albumartist FROM tracks WHERE missing=0").fetchall()
    by_artist: dict[str, list] = {}
    for r in rows:
        by_artist.setdefault((r["albumartist"] or r["artist"] or "").casefold(), []).append(r)
    artist_lang: dict[str, str] = {}
    for a, trs in by_artist.items():
        if a in over:
            artist_lang[a] = over[a]
            continue
        scripts = [script_lang(f"{t['title']} {t['album'] or ''}") for t in trs]
        name_script = script_lang(a)
        if name_script in ("ja", "ko", "zh", "ru"):
            artist_lang[a] = name_script
            continue
        cyr = sum(s == "ru" for s in scripts)
        artist_lang[a] = "ru" if cyr / max(len(trs), 1) >= 0.3 else "en"
    out: dict[int, str] = {}
    for r in rows:
        key = (r["albumartist"] or r["artist"] or "").casefold()
        s = script_lang(f"{r['title']} {r['album'] or ''}")
        out[r["id"]] = artist_lang[key] if key in over else (s if s in ("ru", "ja", "ko", "zh") else artist_lang.get(key, "en"))
    return out


def apply_languages(db: Database, overrides: dict[str, str] | None = None) -> dict:
    """Write detected languages for tracks whose language was not set by the user."""
    detected = detect_languages(db, overrides)
    n = 0
    for tid, lang in detected.items():
        n += db.set_language([tid], lang, src="auto", only_auto=True)
    counts: dict[str, int] = {}
    for lang in detected.values():
        counts[lang] = counts.get(lang, 0) + 1
    return {"tracks": len(detected), "written": n, "counts": counts}


# ---- vocals ----------------------------------------------------------------------------------
def vocal_features(db: Database, tid: int) -> np.ndarray | None:
    from .mood import row_for
    return row_for(db, tid, "both")


def train_vocals(db: Database, models_dir: str | Path, seed: int = 0) -> dict:
    """Instrumental-vs-vocals classifier from the tracks whose ``vocal`` was set by the user / seed (honest, artist-grouped)."""
    import pickle

    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score

    from . import moodcv
    from .mood import _artist_key

    rows = db.conn.execute("SELECT id,vocal FROM tracks WHERE missing=0 AND vocal IS NOT NULL AND vocal_src IN ('manual','seed')").fetchall()
    ids, X, y = [], [], []
    for r in rows:
        f = vocal_features(db, r["id"])
        if f is not None:
            ids.append(r["id"]); X.append(f); y.append(1 if r["vocal"] == "inst" else 0)
    if len(set(y)) < 2 or min(sum(y), len(y) - sum(y)) < 5:
        raise ValueError("Мало примеров: нужно хотя бы по 5 инструментальных и 5 вокальных треков с анализом звука")
    X, y = np.vstack(X), np.array(y)
    groups = [_artist_key(db, i) for i in ids]
    folds, grouped = moodcv.make_folds(groups, 5, seed)

    def fit(Xt, yt):
        m = np.nanmean(Xt, 0); s = np.nanstd(Xt, 0)
        m = np.where(np.isfinite(m), m, 0); s = np.where(np.isfinite(s) & (s > 1e-9), s, 1)
        clf = LogisticRegression(C=0.005, class_weight="balanced", max_iter=3000).fit(np.nan_to_num((Xt - m) / s), yt)
        return m, s, clf

    oof = np.zeros(len(y))
    for tr, va in folds:
        m, s, clf = fit(X[tr], y[tr])
        oof[va] = clf.predict_proba(np.nan_to_num((X[va] - m) / s))[:, 1]
    auc = float(roc_auc_score(y, oof))
    acc = float(((oof >= 0.5) == (y == 1)).mean())
    m, s, clf = fit(X, y)
    info = {"auc": auc, "accuracy": acc, "n": len(y), "instrumental": int(y.sum()), "grouped": grouped}
    Path(models_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(models_dir) / "vocal_model.pkl", "wb") as f:
        pickle.dump({"mean": m, "std": s, "clf": clf, "info": info}, f)
    return info


def predict_vocals(db: Database, models_dir: str | Path, ids: list[int] | None = None, margin: float = 0.0) -> dict:
    """Write instrumental / vocals for tracks without a manual or seed value."""
    import pickle
    path = Path(models_dir) / "vocal_model.pkl"
    if not path.exists():
        raise ValueError("Классификатор вокала ещё не обучен")
    pk = pickle.load(open(path, "rb"))
    todo = ids if ids is not None else [r[0] for r in db.conn.execute(
        "SELECT id FROM tracks WHERE missing=0 AND (vocal IS NULL OR vocal_src='auto')")]
    n = inst = 0
    for tid in todo:
        f = vocal_features(db, tid)
        if f is None:
            continue
        p = float(pk["clf"].predict_proba(np.nan_to_num(((f - pk["mean"]) / pk["std"]).reshape(1, -1)))[0, 1])
        n += db.set_vocal([tid], "inst" if p >= 0.5 + margin else "voc", src="auto", only_auto=True, confidence=p)
        inst += p >= 0.5
    return {"predicted": n, "instrumental": inst}
