"""Ablation of the mood model's features on the REAL labels, with the same artist-grouped folds for every variant.

    python scripts/ablation.py
Metrics: macro ROC AUC and macro average precision (mAP) over moods, out-of-fold.  Logistic regression gets a small
C grid (best C per variant, the same optimism for all); gradient boosting runs on CLAP reduced by PCA + descriptors v2.
"""
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")
import numpy as np  # noqa: E402
from sklearn.metrics import average_precision_score, roc_auc_score  # noqa: E402

from musicplayer.core import mood, moodcv, moodprior  # noqa: E402
from musicplayer.core.db import Database  # noqa: E402
from musicplayer.core.paths import data_dir  # noqa: E402

db = Database(data_dir() / "library.db")
ids_all, _, Y_all, moods = mood.training_matrix(db, 4, "both", 2)
ids = list(ids_all)
rows = {}
for t in ids:
    d = db.get_descriptors(t)
    rows[t] = (mood.feature_vector(d), mood.feature_vector_v2(d),
               np.frombuffer(db.get_embedding(t, "clap"), dtype=np.float32).astype(np.float64))
Y = Y_all
groups = [mood._artist_key(db, i) for i in ids]
folds, _ = moodcv.make_folds(groups, 5, 0)
print(f"{len(ids)} tracks, {len(set(groups))} artists, {len(moods)} moods\n")


def metrics(P):
    aucs, aps = [], []
    for j in range(Y.shape[1]):
        y = Y[:, j]
        if 0 < y.sum() < len(y):
            aucs.append(roc_auc_score(y, P[:, j]))
            aps.append(average_precision_score(y, P[:, j]))
    return float(np.mean(aucs)), float(np.mean(aps))


def zfit(Xtr):
    m, s = np.nanmean(Xtr, 0), np.nanstd(Xtr, 0)
    m, s = np.where(np.isfinite(m), m, 0), np.where(np.isfinite(s) & (s > 1e-9), s, 1)
    return lambda A: np.nan_to_num((A - m) / s)


def oof_logreg(X, C):
    P = np.zeros(Y.shape)
    for tr, va in folds:
        z = zfit(X[tr])
        P[va] = moodcv.PerMoodLogReg(C).fit(z(X[tr]), Y[tr]).predict_proba(z(X[va]))
    return P


def best_logreg(X):
    res = [(metrics(oof_logreg(X, C)), C) for C in (0.0003, 0.001, 0.003, 0.01, 0.05)]
    (auc, ap), C = max(res, key=lambda r: r[0][0])
    return auc, ap, C, oof_logreg(X, C)


def oof_boost(Xd, Xc, n_pca=64):
    from sklearn.decomposition import PCA
    from sklearn.ensemble import HistGradientBoostingClassifier
    P = np.zeros(Y.shape)
    for tr, va in folds:
        zc = zfit(Xc[tr])
        pca = PCA(n_components=n_pca, random_state=0).fit(zc(Xc[tr]))
        F = lambda idx: np.hstack([pca.transform(zc(Xc[idx])), Xd[idx]])  # noqa: E731
        Ftr, Fva = F(tr), F(va)
        for j in range(Y.shape[1]):
            y = Y[tr, j]
            if 0 < y.sum() < len(y):
                clf = HistGradientBoostingClassifier(max_iter=150, learning_rate=0.05, max_leaf_nodes=15,
                                                     l2_regularization=1.0, class_weight="balanced", random_state=0)
                P[va, j] = clf.fit(Ftr, y).predict_proba(Fva)[:, 1]
            else:
                P[va, j] = y.mean()
    return P


D1 = np.vstack([rows[t][0] for t in ids])
D2 = np.vstack([rows[t][1] for t in ids])
CL = np.vstack([rows[t][2] for t in ids])
table = []
for name, X in (("1. только CLAP", CL), ("2a. только дескрипторы v1", D1), ("2b. только дескрипторы v2", D2),
                ("3a. CLAP + дескрипторы v1", np.hstack([D1, CL])), ("3b. CLAP + дескрипторы v2", np.hstack([D2, CL]))):
    auc, ap, C, P = best_logreg(X)
    table.append((name, auc, ap, f"логрегрессия C={C}"))
    print(f"{name:34s} AUC {auc:.3f}  mAP {ap:.3f}  ({table[-1][3]})", flush=True)
    if name.startswith("3b"):
        P3b = P
Pb = oof_boost(D2, CL)
auc, ap = metrics(Pb)
print(f"{'4. бустинг: PCA64(CLAP) + дескр. v2':34s} AUC {auc:.3f}  mAP {ap:.3f}", flush=True)
Pmix = (P3b + Pb) / 2
auc, ap = metrics(Pmix)
print(f"{'5. среднее логрегрессии 3b и бустинга':34s} AUC {auc:.3f}  mAP {ap:.3f}", flush=True)
nb = moodprior.Neighbours(db, moods)
for nm, P in (("3b", P3b), ("5", Pmix)):
    best = max((metrics(moodprior.blend(P, ids, nb, w, 0.0, new_artist=True)), w) for w in np.arange(0, 0.61, 0.05))
    print(f"   {nm} + жанр (новые исполнители, вес {best[1]:.2f}): AUC {best[0][0]:.3f}  mAP {best[0][1]:.3f}")
