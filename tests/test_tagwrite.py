from mutagen.easyid3 import EasyID3

from musicplayer.core import tagwrite
from musicplayer.core.db import split_genres
from musicplayer.core.scanner import scan


def test_write_is_explicit_reversible_and_dry_by_default(db, library, tmp_path):
    scan(db, library, tmp_path / "c")
    f = library / "Beatles" / "Yesterday.mp3"
    tid = db.search(artist="Beatles")[0]["id"]
    db.set_genres([tid], ["Oldies", "Ballad"])
    assert EasyID3(f)["genre"] == ["Pop"]                      # DB edit alone never touches the file
    r = tagwrite.write_genres(db, [tid])                       # dry run
    assert r["dry_run"] and r["would_change"] == 1 and EasyID3(f)["genre"] == ["Pop"]
    r = tagwrite.write_genres(db, [tid], dry_run=False)
    assert r["written"] == 1 and split_genres(EasyID3(f)["genre"]) == ["Oldies", "Ballad"]  # v2.3 joins with "/"
    assert db.genre_changes() == []                            # file and DB agree now
    assert tagwrite.restore_genres(db, [tid])["restored"] == 1
    assert EasyID3(f)["genre"] == ["Pop"]
    assert db.get_track(tid)["genres"] == ["Oldies", "Ballad"]  # DB keeps the user's edit
    assert len(db.genre_changes()) == 1                         # ... and can be written again
