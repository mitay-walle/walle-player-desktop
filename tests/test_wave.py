import random
import time

from musicplayer.core.wave import Wave


def tracks(db, n, artist="a"):
    return [db.upsert_track({"path": f"{artist}{i}.mp3", "title": f"{artist}{i}", "artist": artist}, []) for i in range(n)]


def test_wave_mixes_confirmed_and_guessed_tracks_of_the_chosen_mood(db):
    sad = tracks(db, 20, "sad")
    guess = tracks(db, 10, "guess")
    other = tracks(db, 10, "other")
    db.set_moods(sad, ["Грустное"])
    db.set_moods(other, ["Весёлое"])
    db.suggest_moods([(t, "Грустное", 0.9) for t in guess[:6]] + [(t, "Грустное", 0.2) for t in guess[6:]], "model")
    w = Wave(db, ["Грустное"], explore=0.3, rng=random.Random(1))
    b = w.next_batch(10)
    ids = {t["id"] for t in b}
    assert len(b) == 10 and ids <= set(sad) | set(guess[:6])           # no other mood, no low-confidence guesses
    kinds = [t["wave_kind"] for t in b]
    assert kinds.count("guess") == 3 and kinds.count("known") == 7
    assert all(t["wave_moods"] == ["Грустное"] for t in b)


def test_exclude_recent_and_exhausted_pool(db):
    ids = tracks(db, 6)
    db.set_moods(ids, ["Грустное"])
    w = Wave(db, ["Грустное"], rng=random.Random(2))
    first = w.next_batch(4)
    rest = w.next_batch(10, exclude={t["id"] for t in first})
    assert len(rest) == 2 and not ({t["id"] for t in first} & {t["id"] for t in rest})
    assert w.next_batch(5, exclude=set(ids)) == []
    db.record_play(ids[0], 100, when=time.time() - 60)                # just played: heavily down-weighted
    picks = sum(ids[0] in {t["id"] for t in Wave(db, ["Грустное"], rng=random.Random(s)).next_batch(1)} for s in range(200))
    assert picks < 15                                                  # ~1/6 normally, ~1/100 with the penalty


def test_heard_days_ago_comes_back_rarely_and_slowly_returns():
    """«Not again so soon» lasts days, not two hours: yesterday's track is rare, a month-old one is as welcome as new."""
    from musicplayer.core.wave import recency
    now = time.time()
    day = 86400
    assert recency(None, now) == 1.0
    assert recency(now - 600, now) == 0.02
    assert recency(now - 10 * 3600, now) < 0.15                        # the same day: was 1.0 after two hours
    assert 0.2 < recency(now - day, now) < 0.4
    assert 0.85 < recency(now - 7 * day, now) < 0.95
    assert recency(now - 30 * day, now) > 0.99


def test_tracks_started_on_the_phone_come_back_rarely(db):
    """``heard``: skipped on the phone a minute ago (not a counted play) - the wave started anew avoids it too."""
    ids = tracks(db, 6)
    db.set_moods(ids, ["Грустное"])
    heard = {ids[0]: time.time() - 60, ids[1]: time.time() - 60}
    picks = sum(bool({ids[0], ids[1]} & {t["id"] for t in Wave(db, ["Грустное"], rng=random.Random(s),
                                                                     heard=heard).next_batch(1)}) for s in range(200))
    assert picks < 10                                                  # ~1/3 normally


def test_character_prefers_favourites_or_unfamiliar(db):
    ids = tracks(db, 10)
    db.set_moods(ids, ["Грустное"])
    for t in ids[:5]:
        for _ in range(40):
            db.record_play(t, 100, when=time.time() - 86400 * 30)
    def share_first_half(character):
        n = 0
        for s in range(300):
            n += Wave(db, ["Грустное"], character, rng=random.Random(s)).next_batch(1)[0]["id"] in ids[:5]
        return n / 300
    assert share_first_half("favorite") > 0.6 and share_first_half("unfamiliar") < 0.15
    assert 0.35 < share_first_half("mixed") < 0.65


def test_like_confirms_a_guess_and_dislike_teaches_a_negative(db):
    ids = tracks(db, 4)
    db.suggest_moods([(t, "Грустное", 0.9) for t in ids], "model")
    w = Wave(db, ["Грустное"], explore=1.0, rng=random.Random(3))
    b = {t["id"]: t for t in w.next_batch(4)}
    assert all(t["wave_kind"] == "guess" for t in b.values())
    r = w.like(b[ids[0]])
    assert r == {"result": "confirmed", "moods": ["Грустное"]} and db.get_track(ids[0])["moods"] == ["Грустное"]
    assert w.like(b[ids[0]])["result"] == "liked"                      # already known: nothing to confirm
    r = w.dislike(b[ids[1]])
    assert r["result"] == "rejected" and db.mood_negatives() == {ids[1]: {"Грустное"}}
    assert db.get_track(ids[1])["mood_suggestions"] == []
    known = Wave(db, ["Грустное"]).next_batch(5)
    assert {t["id"] for t in known} == {ids[0]} | ({ids[2], ids[3]} & {t["id"] for t in known})


def test_wave_without_moods_uses_all_labelled_moods(db):
    a, b = tracks(db, 3, "a"), tracks(db, 3, "b")
    db.set_moods(a, ["Грустное"])
    db.set_moods(b, ["Весёлое"])
    got = {t["id"] for t in Wave(db, None, rng=random.Random(4)).next_batch(10)}
    assert got == set(a) | set(b)


def test_wave_by_genres(db):
    rock = [db.upsert_track({"path": f"r{i}.mp3", "title": f"r{i}", "artist": "r"}, ["Rock"]) for i in range(6)]
    pop = [db.upsert_track({"path": f"p{i}.mp3", "title": f"p{i}", "artist": "p"}, ["Pop"]) for i in range(6)]
    b = Wave(db, genres=["rock"], rng=random.Random(3)).next_batch(20)         # genres only: every rock track
    assert {t["id"] for t in b} == set(rock) and all(t["wave_kind"] == "known" for t in b)
    db.set_moods(rock[:2] + pop[:2], ["Грустное"])
    b = Wave(db, ["Грустное"], genres=["Rock"], rng=random.Random(4)).next_batch(20)   # mood within the genre
    assert {t["id"] for t in b} == set(rock[:2])


def test_unfamiliar_leaves_out_liked_tracks(db):
    from musicplayer.core import likes
    ids = tracks(db, 8)
    db.set_moods(ids, ["Грустное"])
    likes.set_liked(db, "owner", "track", str(ids[0]), True)
    likes.set_liked(db, "guest1", "track", str(ids[1]), True)
    got = {t["id"] for t in Wave(db, ["Грустное"], "unfamiliar").next_batch(20)}
    assert got == set(ids) - {ids[0]}                                        # the owner's like
    got = {t["id"] for t in Wave(db, ["Грустное"], "unfamiliar", user="guest1").next_batch(20)}
    assert got == set(ids) - {ids[1]}                                        # a guest's own like
    assert ids[0] in {t["id"] for t in Wave(db, ["Грустное"], "mixed").next_batch(20)}


def test_unfamiliar_leaves_out_liked_artists_and_albums(db):
    from musicplayer.core import likes
    fav = tracks(db, 3, "Fav")
    duet = db.upsert_track({"path": "duet.mp3", "title": "duet", "artist": "Fav & Other"}, [])
    alb = [db.upsert_track({"path": f"alb{i}.mp3", "title": f"alb{i}", "artist": "Someone", "album": "Best"}, [])
           for i in range(2)]
    rest = tracks(db, 4, "rest")
    db.set_moods(fav + [duet] + alb + rest, ["Грустное"])
    likes.set_liked(db, "owner", "artist", "fav", True)
    likes.set_liked(db, "owner", "album", "Someone\x1fBest", True)
    got = {t["id"] for t in Wave(db, ["Грустное"], "unfamiliar").next_batch(20)}
    assert got == set(rest)


def test_wave_by_language_and_voice(db):
    ru = tracks(db, 4, "ru")
    en = tracks(db, 4, "en")
    db.set_language(ru, "ru")
    db.set_language(en, "en")
    db.set_vocal(ru[:2], "inst")
    db.set_flags(en[:2], ["female"])
    assert {t["id"] for t in Wave(db, langs=["ru"]).next_batch(20)} == set(ru)          # no moods: every ru track
    assert {t["id"] for t in Wave(db, langs=["foreign"]).next_batch(20)} == set(en)
    assert {t["id"] for t in Wave(db, voices=["inst"]).next_batch(20)} == set(ru[:2])
    assert {t["id"] for t in Wave(db, voices=["female"], langs=["en"]).next_batch(20)} == set(en[:2])
    assert {t["id"] for t in Wave(db, voices=["voc"]).next_batch(20)} == set(en[:2])     # a known voice = vocals
    db.set_moods(ru + en, ["Грустное"])
    assert {t["id"] for t in Wave(db, ["Грустное"], langs=["en"]).next_batch(20)} == set(en)
    assert Wave(db, genres=["Jazz"]).next_batch(20) == []                                # nothing of it: no wave


def test_radio_from_a_track_and_from_an_artist(db):
    import numpy as np
    rock = [db.upsert_track({"path": f"r{i}.mp3", "title": f"r{i}", "artist": f"r{i % 2}"}, ["Rock"]) for i in range(8)]
    pop = [db.upsert_track({"path": f"p{i}.mp3", "title": f"p{i}", "artist": "p"}, ["Pop"]) for i in range(8)]
    rng = np.random.default_rng(0)
    base_r, base_p = rng.normal(size=16), rng.normal(size=16)
    for t in rock:
        db.set_embedding(t, "clap", (base_r + 0.1 * rng.normal(size=16)).astype(np.float32).tobytes())
    for t in pop:
        db.set_embedding(t, "clap", (base_p + 0.1 * rng.normal(size=16)).astype(np.float32).tobytes())
    w = Wave(db, seed_track=rock[0], rng=random.Random(1))
    b = w.next_batch(5)
    assert len(b) == 5 and rock[0] not in {t["id"] for t in b}
    near = sum(t["id"] in rock for s in range(30) for t in Wave(db, seed_track=rock[0], rng=random.Random(s)).next_batch(3))
    assert near > 70                                                          # mostly the rock side
    b = Wave(db, seed_artist="p", rng=random.Random(2)).next_batch(16)
    assert set(pop) <= {t["id"] for t in b}                                   # the artist's own songs are in his radio
