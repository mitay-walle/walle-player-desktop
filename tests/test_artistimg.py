"""Artist pictures: Deezer's photo kept next to the covers, the biggest album's cover meanwhile (no network here)."""

import time

from starlette.testclient import TestClient

from musicplayer.core import artistimg
from musicplayer.core.db import Database
from musicplayer.server.api import Access, Library, build_app

KEY = "test-key-123"
AUTH = {"Authorization": f"Bearer {KEY}"}
USERS = [{"id": "owner", "name": "Я", "token": KEY, "owner": True}]
JPEG = b"\xff\xd8photo"


def _lib(tmp_path):
    db = Database(tmp_path / "library.db")
    for i, (artist, cover) in enumerate([("AC/DC", "aaaaaa01"), ("AC/DC", "aaaaaa01"), ("AC/DC", "bbbbbb02"),
                                         ("Кино", None)]):
        db.upsert_track({"path": str(tmp_path / f"{i}.mp3"), "title": f"t{i}", "artist": artist, "albumartist": artist,
                         "album": f"al{cover}", "year": 2000, "track_no": i, "disc_no": 1, "duration": 200.0,
                         "mtime": 0, "size": 1, "cover_key": cover}, [])
    assert db.artist_covers() == {"AC/DC": "aaaaaa01"} and db.artist_covers("Кино") == {}
    db.close()
    covers = tmp_path / "covers"
    covers.mkdir()
    (covers / "aaaaaa01.jpg").write_bytes(b"\xff\xd8album")
    return Library(tmp_path / "library.db", covers), covers


def test_photo_only_for_the_same_name(monkeypatch):
    import io
    import json
    found = {"data": [{"name": "Chester Bennington", "nb_fan": 9, "picture_xl": "https://x/artist/1/a.jpg"},
                      {"name": "linkin  park", "nb_fan": 5, "picture_xl": "https://x/artist//placeholder.jpg"},
                      {"name": "Linkin Park", "nb_fan": 7, "picture_xl": "https://x/artist/2/b.jpg"}]}
    monkeypatch.setattr(artistimg.urllib.request, "urlopen", lambda req, timeout: io.BytesIO(json.dumps(found).encode()))
    assert artistimg.find("Linkin Park") == "https://x/artist/2/b.jpg"   # not the placeholder, not another artist
    assert artistimg.find("Кино") is None


def test_server_gives_the_album_cover_then_the_photo(tmp_path, monkeypatch):
    lib, covers = _lib(tmp_path)

    def fake_fetch(c, name, timeout=10):
        if name == "Кино":
            (artistimg.folder(c) / f"{artistimg.key(name)}.none").write_bytes(b"")
            return None
        f = artistimg.folder(c) / f"{artistimg.key(name)}.jpg"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(JPEG)
        return f
    monkeypatch.setattr(artistimg, "fetch", fake_fetch)
    c = TestClient(build_app(lib, Access(USERS), {"name": "test"}))
    items = c.get("/api/artists", headers=AUTH).json()["items"]
    assert {i["name"]: i["image"] for i in items}["AC/DC"] == "/api/artist_image?name=AC%2FDC"
    r = c.get("/api/artist_image?name=AC%2FDC", headers=AUTH)
    assert r.content == b"\xff\xd8album" and "max-age=3600" in r.headers["cache-control"]   # meanwhile the album's
    for _ in range(50):                                                                    # the photo comes
        if artistimg.cached(covers, "AC/DC"):
            break
        time.sleep(0.05)
    assert c.get("/api/artist_image?name=AC%2FDC", headers=AUTH).content == JPEG
    assert c.get("/api/artist_image?name=%D0%9A%D0%B8%D0%BD%D0%BE", headers=AUTH).status_code == 404  # nothing at all
    assert c.get("/api/artist_image?name=AC%2FDC").status_code in (401, 403)                         # a key is needed
    lib.close()
