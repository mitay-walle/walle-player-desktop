"""Honest evaluation and model selection for the mood classifier.

* Cross-validation is GROUPED BY ARTIST: tracks of one artist never sit in both train and validation, otherwise the
  model "recognises the artist" and the score is inflated (a random split gave AUC 0.93 where grouping shows less).
* Candidates (logistic regression with several C, MLP) are compared on out-of-fold predictions; the best by macro AUC
  (then top-1) is refitted on all labels.
* From the out-of-fold predictions of the winner: Platt calibration per mood, so a
  shown "80%" means roughly 80% of such suggestions were right.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*Stochastic Optimizer.*")


class PerMoodLogReg:
    """One-vs-rest logistic regression that tolerates moods with a single class in a training fold."""

    def __init__(self, C: float = 1.0) -> None:
        self.C = C
        self.models: list = []
        self.priors: list[float] = []

    def fit(self, X: np.ndarray, Y: np.ndarray) -> "PerMoodLogReg":
        from sklearn.linear_model import LogisticRegression
        self.models, self.priors = [], []
        for j in range(Y.shape[1]):
            y = Y[:, j]
            self.priors.append(float(y.mean()))
            if 0 < y.sum() < len(y):
                self.models.append(LogisticRegression(C=self.C, class_weight="balanced", max_iter=3000).fit(X, y))
            else:
                self.models.append(None)
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        cols = [np.full(len(X), p) if m is None else m.predict_proba(X)[:, 1] for m, p in zip(self.models, self.priors)]
        return np.vstack(cols).T


class SplitEnsemble:
    """Two logistic regressions - one on the first ``n_first`` columns (audio descriptors), one on the rest (CLAP
    embedding) - averaged.  A 512-d embedding drowns 45 descriptors in one concatenated model; separate models
    keep both voices."""

    def __init__(self, n_first: int, C1: float = 0.5, C2: float = 0.05, w: float = 0.5) -> None:
        self.n, self.C1, self.C2, self.w = n_first, C1, C2, w

    def fit(self, X: np.ndarray, Y: np.ndarray) -> "SplitEnsemble":
        self.a = PerMoodLogReg(self.C1).fit(X[:, :self.n], Y)
        self.b = PerMoodLogReg(self.C2).fit(X[:, self.n:], Y)
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return self.w * self.a.predict_proba(X[:, :self.n]) + (1 - self.w) * self.b.predict_proba(X[:, self.n:])


class MlpNet:
    """sklearn MLP (multi-label) with the same predict_proba interface."""

    def __init__(self, hidden: int = 48, alpha: float = 1e-2, epochs: int = 150, seed: int = 0) -> None:
        self.hidden, self.alpha, self.epochs, self.seed = hidden, alpha, epochs, seed
        self.net = None

    def fit(self, X: np.ndarray, Y: np.ndarray) -> "MlpNet":
        from sklearn.neural_network import MLPClassifier
        self.net = MLPClassifier(hidden_layer_sizes=(self.hidden,), alpha=self.alpha, learning_rate_init=3e-3,
                                 max_iter=self.epochs, random_state=self.seed, batch_size=min(32, len(X)))
        self.net.fit(X, Y)
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return np.asarray(self.net.predict_proba(X))


@dataclass
class Candidate:
    name: str
    make: callable
    metrics: dict = field(default_factory=dict)


def default_candidates(models: tuple[str, ...] = ("logreg", "mlp"), epochs: int = 150, hidden: int = 48,
                       n_desc: int | None = None) -> list[Candidate]:
    """``n_desc``: number of leading descriptor columns when features are descriptors+CLAP (enables the ensemble)."""
    out = []
    if "logreg" in models and n_desc:
        out += [Candidate("ансамбль дескрипторы+CLAP", lambda: SplitEnsemble(n_desc, 0.5, 0.05)),
                Candidate("ансамбль (вес дескрипторов 0.7)", lambda: SplitEnsemble(n_desc, 0.5, 0.05, 0.7))]
    if "logreg" in models:
        out += [Candidate(f"логрегрессия C={c}", lambda c=c: PerMoodLogReg(c)) for c in (0.0003, 0.0007, 0.001, 0.002, 0.005, 0.02)]
    if "mlp" in models:
        out += [Candidate("нейросеть MLP", lambda: MlpNet(hidden, 1e-2, epochs))]
    return out


def make_folds(groups: list[str], k: int = 5, seed: int = 0) -> tuple[list[tuple[np.ndarray, np.ndarray]], bool]:
    """Folds that keep each group (artist) on one side.  Falls back to random folds if there are < 3 groups."""
    n = len(groups)
    rng = np.random.RandomState(seed)
    uniq = sorted(set(groups))
    if len(uniq) < 3:
        idx = rng.permutation(n)
        parts = np.array_split(idx, min(k, max(2, n // 2)))
        return [(np.setdiff1d(np.arange(n), p), p) for p in parts], False
    order = list(rng.permutation(len(uniq)))
    kk = min(k, len(uniq))
    fold_of = {uniq[g]: i % kk for i, g in enumerate(order)}
    f = np.array([fold_of[g] for g in groups])
    return [(np.where(f != i)[0], np.where(f == i)[0]) for i in range(kk)], True


def macro_metrics(Y: np.ndarray, P: np.ndarray, thr: np.ndarray | float = 0.5) -> dict:
    from sklearn.metrics import f1_score, roc_auc_score
    aucs = [roc_auc_score(Y[:, j], P[:, j]) for j in range(Y.shape[1]) if 0 < Y[:, j].sum() < len(Y)]
    pred = (P >= thr).astype(int)
    has = Y.sum(axis=1) > 0
    top1 = float(np.mean(Y[np.arange(len(Y)), P.argmax(axis=1)][has])) if has.any() else 0.0
    return {"auc": float(np.mean(aucs)) if aucs else 0.0, "f1": float(f1_score(Y, pred, average="macro", zero_division=0)),
            "top1": top1}


def oof_predictions(cand: Candidate, Z_fn, Y: np.ndarray, folds) -> np.ndarray:
    """Out-of-fold probabilities.  ``Z_fn(train_idx)`` returns (standardise function) fitted on the train part only."""
    P = np.zeros(Y.shape, dtype=float)
    for tr, va in folds:
        tf = Z_fn(tr)
        P[va] = cand.make().fit(tf(tr), Y[tr]).predict_proba(tf(va))
    return P


def fit_platt(Y: np.ndarray, P: np.ndarray) -> list[tuple[float, float]]:
    """Per-mood Platt scaling p' = sigmoid(a*logit(p)+b) fitted on out-of-fold predictions."""
    from sklearn.linear_model import LogisticRegression
    out = []
    for j in range(Y.shape[1]):
        y = Y[:, j]
        if 0 < y.sum() < len(y):
            z = np.log(np.clip(P[:, j], 1e-4, 1 - 1e-4) / (1 - np.clip(P[:, j], 1e-4, 1 - 1e-4))).reshape(-1, 1)
            m = LogisticRegression(C=10.0).fit(z, y)
            out.append((float(m.coef_[0, 0]), float(m.intercept_[0])))
        else:
            out.append((1.0, 0.0))
    return out


def apply_platt(P: np.ndarray, platt: list[tuple[float, float]]) -> np.ndarray:
    Pc = np.clip(P, 1e-4, 1 - 1e-4)
    z = np.log(Pc / (1 - Pc))
    a = np.array([p[0] for p in platt])
    b = np.array([p[1] for p in platt])
    return 1.0 / (1.0 + np.exp(-(z * a + b)))


def expected_calibration_error(Y: np.ndarray, P: np.ndarray, bins: int = 8) -> float:
    """Mean |confidence - accuracy| over probability bins (all moods pooled)."""
    y, p = Y.ravel(), P.ravel()
    edges = np.linspace(0, 1, bins + 1)
    ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi if hi < 1 else p <= hi)
        if m.any():
            ece += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(ece)


AUTO_PRESETS = {
    #            floor precision, prevalence multiplier, minimum AUC of the mood
    "strict":   (0.55, 0.0, 0.0),
    "balanced": (0.35, 2.5, 0.66),
    "wide":     (0.30, 2.0, 0.64),
}
AUTO_MODE_LABELS = {"strict": "строго", "balanced": "сбалансировано", "wide": "широко"}


def auto_thresholds(Y: np.ndarray, P: np.ndarray, moods: list[str], preset: str = "balanced",
                    min_hits: int = 6) -> dict[str, float | None]:
    """Per-mood probability from which a prediction may become the track's mood automatically.

    From out-of-fold calibrated predictions: the smallest threshold whose precision reaches
    ``max(floor, prevalence * multiplier)`` with >= ``min_hits`` predictions.  ``None`` when the model cannot reach it,
    or when its AUC for that mood is below the preset's minimum (the mood stays suggestion-only).  A frequent mood
    therefore needs a higher probability than a rare one, so it cannot swallow the whole library."""
    from sklearn.metrics import roc_auc_score
    floor, mult, min_auc = AUTO_PRESETS[preset]
    out: dict[str, float | None] = {}
    for j, name in enumerate(moods):
        y, p = Y[:, j], P[:, j]
        if not 0 < y.sum() < len(y) or (min_auc and roc_auc_score(y, p) < min_auc):
            out[name] = None
            continue
        target = min(0.75, max(floor, float(y.mean()) * mult)) if mult else floor
        found = None
        for thr in np.linspace(0.1, 0.95, 35):
            sel = p >= thr
            if sel.sum() >= min_hits and y[sel].mean() >= target:
                found = round(float(thr), 3)
                break
        out[name] = found
    return out
