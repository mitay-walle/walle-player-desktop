import json

import numpy as np

from musicplayer.core import webeval
from musicplayer.core.webtags import _ensure


class FakeModel:
    moods = ["Грустное", "Весёлое"]
    info = {"mode": "descriptors"}

    def predict_proba(self, X):
        return np.array([[x[0], 1 - x[0]] for x in X])


def test_agreement_against_acousticbrainz(db, monkeypatch):
    _ensure(db)
    for i in range(1, 13):
        db.conn.execute("INSERT INTO tracks(id,path,title,artist,missing) VALUES(?,?,?,?,0)", (i, f"/m/{i}.mp3", f"t{i}", "a"))
        sad = 0.9 if i <= 6 else 0.1
        db.conn.execute("INSERT INTO track_web VALUES(?,?,?,?,?)",
                        (i, "x", json.dumps({"mood_sad": sad, "mood_happy": 1 - sad}), "[]", 0))
    monkeypatch.setattr(webeval, "row_for", lambda db, t, mode, vocab=None, feat_v=1: np.array([0.8 if t <= 6 else 0.2]))
    truth = webeval.web_truth(db)
    assert sum(truth["Грустное"].values()) == 6 and len(truth["Весёлое"]) == 12
    res = webeval.agreement(db, FakeModel(), truth)
    assert res["per_mood"]["Грустное"]["auc"] == 1.0 and res["mean_auc"] == 1.0
