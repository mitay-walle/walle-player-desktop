from musicplayer.core import vocalgender
from musicplayer.core.flags import apply_detected


class FakeHttp:
    def __init__(self, pages):
        self.pages = pages

    def get_json(self, url):
        return next((v for k, v in self.pages.items() if k in url), None)


def test_band_vocalist_gender_and_flags_survive_title_detection(db, library, tmp_path):
    from musicplayer.core.scanner import scan
    scan(db, library, tmp_path / "covers")
    tid = db.search(limit=1)[0]["id"]
    artist = db.get_track(tid)["artist"]
    http = FakeHttp({
        "/artist/?": {"artists": [{"id": "band", "score": 100}]},
        "/artist/band?": {"type": "Group", "relations": [
            {"type": "member of band", "direction": "backward", "attributes": ["lead vocals"], "ended": False,
             "artist": {"id": "singer"}},
            {"type": "member of band", "direction": "backward", "attributes": ["guitar"], "artist": {"id": "guitarist"}}]},
        "/artist/singer?": {"type": "Person", "gender": "Female", "relations": []},
    })
    assert vocalgender.vocal_genders(db, http, artist) == {"female"}
    vocalgender.tag_library(db, http=http)
    assert "female" in db.get_track(tid)["flags"]
    apply_detected(db)                                 # title-based detection keeps the vocal flags
    assert "female" in db.get_track(tid)["flags"]


def test_longest_serving_vocalist_wins():
    from musicplayer.core.vocalgender import tenure
    chester = {"begin": "1999", "end": "2017", "ended": True, "lead": True}
    emily = {"begin": "2024", "end": None, "ended": False, "lead": True}
    assert tenure(chester) == 18 and tenure(emily) == 2
