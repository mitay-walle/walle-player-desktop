import json

import numpy as np

from musicplayer.core import webtags as wt

REC_ID = "11111111-1111-1111-1111-111111111111"
OTHER_ID = "22222222-2222-2222-2222-222222222222"


class FakeHttp:
    """Canned MusicBrainz / AcousticBrainz / Last.fm answers; counts calls."""

    def __init__(self):
        self.calls = []

    def get_json(self, url, retries=3):
        self.calls.append(url)
        if "musicbrainz.org/ws/2/recording" in url:
            return {"recordings": [{"id": REC_ID, "score": 100}, {"id": OTHER_ID, "score": 60}]} if "Sad+Song" in url else {"recordings": []}
        if "musicbrainz.org/ws/2/artist/?" in url:
            return {"artists": [{"id": "art-1", "score": 100}]} if "Sad+Band" in url else {"artists": []}
        if "musicbrainz.org/ws/2/artist/art-1" in url:
            return {"tags": [{"name": "Shoegaze", "count": 5}, {"name": "sad", "count": 3}], "genres": [{"name": "shoegaze", "count": 7}]}
        if "acousticbrainz.org" in url:
            hl = {"mood_sad": {"all": {"sad": 0.91, "not_sad": 0.09}}, "mood_happy": {"all": {"happy": 0.05, "not_happy": 0.95}},
                  "mood_relaxed": {"all": {"relaxed": 0.8, "not_relaxed": 0.2}}, "mood_acoustic": {"all": {"acoustic": 0.6, "not_acoustic": 0.4}}}
            return {REC_ID: {"0": {"highlevel": hl}}}
        if "audioscrobbler" in url:
            return {"toptags": {"tag": [{"name": "Melancholy", "count": 100}, {"name": "rock", "count": 50}]}}
        return None


def add(db, title, artist):
    return db.upsert_track({"path": f"{artist}-{title}.mp3", "title": title, "artist": artist}, [])


def test_fetch_stores_acousticbrainz_moods_and_artist_tags_and_caches(db):
    a = add(db, "Sad Song (feat. Guest)", "Sad Band")
    b = add(db, "Unknown", "Nobody")
    http = FakeHttp()
    r = wt.fetch_tracks(db, [a, b], http=http)
    assert r == {"fetched": 2, "with_acousticbrainz": 1, "with_tags": 1}
    web = wt.get_web(db, a)
    assert web["mbid"] == REC_ID and web["ab"]["mood_sad"] == 0.91 and web["ab"]["mood_happy"] == 0.05
    assert ["shoegaze", 7] in web["tags"] and wt.get_web(db, b)["ab"] == {}
    n = len(http.calls)
    wt.fetch_tracks(db, [a, b], http=http)                           # nothing left to fetch
    assert len(http.calls) == n
    c = add(db, "Sad Song", "Sad Band")                              # same artist/title: all lookups come from the cache
    wt.fetch_tracks(db, [c], http=http)
    assert len(http.calls) == n and wt.get_web(db, c)["mbid"] == REC_ID


def test_title_is_cleaned_before_searching(db):
    a = add(db, "Sad Song (feat. Guest)", "Sad Band")
    http = FakeHttp()
    wt.fetch_tracks(db, [a], http=http)
    assert "Sad+Song" in http.calls[0] and "Guest" not in http.calls[0]


def test_suggestions_agreement_and_never_override_labels(db):
    a = add(db, "Sad Song", "Sad Band")
    wt.fetch_tracks(db, [a], http=FakeHttp())
    r = wt.suggest_from_web(db, min_conf=0.7)
    assert r["tracks"] == 1
    sug = {s["mood"]: s for s in db.get_track(a)["mood_suggestions"]}
    assert set(sug) == {"Грустное", "Спокойное"} and sug["Грустное"]["source"] == "web" and sug["Грустное"]["confidence"] == 0.91
    assert db.get_track(a)["moods"] == []
    db.set_moods([a], ["Грустное"])
    ag = wt.agreement(db)
    assert ag["strong_guesses"] == 2 and ag["correct"] == 1 and ag["precision"] == 0.5
    assert ag["per_mood"]["Грустное"]["precision"] == 1.0


def test_tag_keywords_map_to_moods_and_lastfm_is_optional(db):
    m = wt.tags_to_moods([["melancholy", 100], ["rock", 50]])
    assert set(m) == {"Грустное"} and 0 < m["Грустное"] <= 1
    assert wt.tags_to_moods([]) == {}
    a = add(db, "Sad Song", "Sad Band")
    http = FakeHttp()
    wt.fetch_tracks(db, [a], http=http, lastfm_key="KEY")
    assert any("audioscrobbler" in c for c in http.calls)
    assert any(t[0] == "melancholy" for t in wt.get_web(db, a)["tags"])
    assert "KEY" not in "".join(c for c in http.calls if "musicbrainz" in c)    # the key goes only to Last.fm


def test_web_vector_shape_and_missing_data(db):
    a = add(db, "Sad Song", "Sad Band")
    wt.fetch_tracks(db, [a], http=FakeHttp())
    vocab = wt.top_tags(db, 5)
    v = wt.web_vector(wt.get_web(db, a), vocab)
    assert v.shape == (len(wt.AB_FEATURES) + len(vocab) + 1,) and v[-1] == 1.0 and abs(v[0] - 0.91) < 1e-9
    none = wt.web_vector(None, vocab)
    assert np.isnan(none[:len(wt.AB_FEATURES)]).all() and none[-1] == 0.0


def _artist_tag_world(db, informative: bool, n_artists=14, per=8, seed=7):
    """Moods unrelated to sound (descriptors are noise), but each artist has genre tags; if informative, the tags
    decide the mood (metal -> aggressive, ambient -> calm), otherwise they are random."""
    import json
    rng = np.random.RandomState(seed)
    from musicplayer.core.descriptors import DESCRIPTOR_NAMES
    wt._ensure(db)
    for a in range(n_artists):
        heavy = a % 2 == 0
        mood = "Злое" if heavy else "Спокойное"
        tags = [["metal", 9], ["hardcore", 4]] if (heavy == informative or (not informative and rng.rand() < .5)) else [["ambient", 9], ["chillout", 4]]
        if not informative:
            tags = [["metal", 9]] if rng.rand() < .5 else [["ambient", 9]]
        for i in range(per):
            d = {n: None for n in DESCRIPTOR_NAMES}
            d.update(bpm=100 + rng.normal(0, 20), loudness=-15 + rng.normal(0, 3), spectral_centroid=2000 + rng.normal(0, 500))
            tid = db.upsert_track({"path": f"w{a}_{i}.mp3", "title": f"t{i}", "artist": f"Artist {a}"}, [])
            db.set_descriptors(tid, d)
            db.set_moods([tid], [mood])
            db.conn.execute("INSERT INTO track_web VALUES(?,?,?,?,?)", (tid, None, "{}", json.dumps(tags), 0))


def test_training_uses_internet_tags_only_when_they_help(db, tmp_path):
    from musicplayer.core import mood
    _artist_tag_world(db, informative=True)
    r = mood.train(db, tmp_path, epochs=40)
    assert r["web_vocab"] and r["web_gain_auc"] > 0.2 and r["val"]["auc"] > 0.9       # tags decide the mood: used
    assert any("интернет" in n for n in r["cv"]) or r["web_vocab"]


def test_uninformative_tags_are_not_used(db, tmp_path):
    from musicplayer.core import mood
    _artist_tag_world(db, informative=False, n_artists=30)
    r = mood.train(db, tmp_path, epochs=40)
    assert r["web_vocab"] is None or r["web_gain_auc"] <= 0.05                      # nothing to learn from random tags


def test_web_features_flow_into_prediction(db, tmp_path):
    from musicplayer.core import mood
    _artist_tag_world(db, informative=True)
    new = db.upsert_track({"path": "new.mp3", "title": "n", "artist": "Brand New"}, [])
    from musicplayer.core.descriptors import DESCRIPTOR_NAMES
    d = {n: None for n in DESCRIPTOR_NAMES}
    d.update(bpm=100, loudness=-15, spectral_centroid=2000)
    db.set_descriptors(new, d)
    db.conn.execute("INSERT INTO track_web VALUES(?,?,?,?,?)", (new, None, "{}", json.dumps([["metal", 8]]), 0))
    mood.train(db, tmp_path, epochs=40)
    mood.predict(db, tmp_path)
    assert db.get_track(new)["mood_suggestions"][0]["mood"] == "Злое"       # predicted from its tags alone


def test_acousticbrainz_negative_class_names():
    from musicplayer.core.webtags import is_negative_class
    assert is_negative_class("Not sad") and is_negative_class("not_relaxed") and is_negative_class("non_electronic")
    assert not is_negative_class("Sad") and not is_negative_class("Party") and not is_negative_class("happy")
