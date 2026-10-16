"""SQLite catalogue: tracks, genres, moods (confirmed + suggested), plays, playlists, FTS5 search.

Genre and mood edits live only in the database; ``tracks.genres_file`` keeps what the file
tag held at scan time so differences can be written to files by a separate explicit step.

Mood label layers (``track_moods.source``):

* ``manual`` - set or confirmed by the user (highest trust);
* ``seed``   - bootstrap labels made by Claude from audio signals + knowledge of the music;
* ``auto``   - assigned by the trained model above a confidence threshold (shown as labels, never used for training);
* ``model`` / ``claude`` / ``web`` / ``clap`` - suggestions below the threshold, waiting for a yes/no.

manual + seed are the training set; manual + seed + auto are what search, playlists and the wave treat as the
track's moods.
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from .feat import parse as parse_feat

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks(
  id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, mtime REAL, size INTEGER,
  title TEXT, artist TEXT, albumartist TEXT, album TEXT, year INTEGER,
  track_no INTEGER, disc_no INTEGER, duration REAL, cover_key TEXT,
  genres_file TEXT DEFAULT '', play_count INTEGER DEFAULT 0, last_played REAL,
  added REAL, missing INTEGER DEFAULT 0,
  lang TEXT, lang_src TEXT, vocal TEXT, vocal_src TEXT, vocal_conf REAL, flags_src TEXT);
CREATE INDEX IF NOT EXISTS ix_tracks_artist ON tracks(artist);
CREATE INDEX IF NOT EXISTS ix_tracks_album ON tracks(albumartist, album);
CREATE TABLE IF NOT EXISTS track_genres(track_id INTEGER, genre TEXT, PRIMARY KEY(track_id, genre));
CREATE TABLE IF NOT EXISTS track_artists(track_id INTEGER, name TEXT COLLATE UNI, role TEXT DEFAULT 'feat',
  PRIMARY KEY(track_id, name, role));
CREATE INDEX IF NOT EXISTS ix_track_artists_name ON track_artists(name);
CREATE TABLE IF NOT EXISTS track_flags(track_id INTEGER, flag TEXT, PRIMARY KEY(track_id, flag));
CREATE INDEX IF NOT EXISTS ix_track_flags ON track_flags(flag);
CREATE TABLE IF NOT EXISTS moods(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL COLLATE UNI, prompt TEXT);
CREATE TABLE IF NOT EXISTS track_moods(track_id INTEGER, mood_id INTEGER,
  source TEXT DEFAULT 'manual', confidence REAL, PRIMARY KEY(track_id, mood_id));
CREATE TABLE IF NOT EXISTS mood_rejects(track_id INTEGER, mood_id INTEGER, at REAL, PRIMARY KEY(track_id, mood_id));
CREATE TABLE IF NOT EXISTS favorites(kind TEXT, key TEXT, added REAL, PRIMARY KEY(kind, key));
CREATE TABLE IF NOT EXISTS hidden(user TEXT, kind TEXT, key TEXT COLLATE UNI, added REAL, PRIMARY KEY(user, kind, key));
CREATE TABLE IF NOT EXISTS custom_genres(name TEXT PRIMARY KEY COLLATE UNI, parent TEXT, added REAL);
CREATE TABLE IF NOT EXISTS plays(id INTEGER PRIMARY KEY, track_id INTEGER, played_at REAL, listened REAL);
CREATE INDEX IF NOT EXISTS ix_plays_track ON plays(track_id, played_at);
CREATE TABLE IF NOT EXISTS playlists(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, rules TEXT);
CREATE TABLE IF NOT EXISTS playlist_tracks(playlist_id INTEGER, pos INTEGER, track_id INTEGER);
CREATE TABLE IF NOT EXISTS track_descriptors(track_id INTEGER PRIMARY KEY, data TEXT, version INTEGER);
CREATE TABLE IF NOT EXISTS track_embeddings(track_id INTEGER, model TEXT, vec BLOB, PRIMARY KEY(track_id, model));
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(title, artist, album, genres, moods, year,
  tokenize='unicode61 remove_diacritics 2');
"""

from .moods import DEFAULT_MOODS, MOOD_DROPPED, MOOD_RENAMES  # noqa: E402


def canonical_mood(name: str) -> str | None:
    """Current name of a mood: renamed / merged moods follow MOOD_RENAMES, dropped ones give None."""
    seen = set()
    while name in MOOD_RENAMES and name not in seen:
        seen.add(name)
        name = MOOD_RENAMES[name]
    return None if name in MOOD_DROPPED else name

PERIODS = {"week": 7, "month": 30, "quarter": 91, "half_year": 182, "year": 365}
TRACK_COLS = ("id,path,title,artist,albumartist,album,year,track_no,disc_no,duration,"
              "cover_key,play_count,last_played,added,lang,vocal,era,edited")
CONFIRMED = "manual"
LABEL_SOURCES = ("manual", "seed", "auto")          # shown / searched as the track's moods
TRAIN_SOURCES = ("manual", "seed")                   # ground truth for training
_LS = "('manual','seed','auto')"
_TS = "('manual','seed')"


def split_genres(value: str | Iterable[str] | None) -> list[str]:
    if not value:
        return []
    items = [value] if isinstance(value, str) else list(value)
    out: list[str] = []
    for it in items:
        for g in re.split(r"[;/]", it):
            g = g.strip()
            if g and g.lower() not in (x.lower() for x in out):
                out.append(g)
    return out


def rare_weights(play_counts: list[int], power: float = 1.0) -> list[float]:
    """Sampling weight ~ 1/(1+plays)^power: rarely played tracks come up first."""
    return [1.0 / (1.0 + c) ** power for c in play_counts]


def weighted_order(rows: list[dict], power: float = 1.0, rng: random.Random | None = None) -> list[dict]:
    """Weighted random permutation (Efraimidis-Spirakis) favouring rarely played tracks."""
    rng = rng or random
    w = rare_weights([r.get("play_count") or 0 for r in rows], power)
    keyed = [(rng.random() ** (1.0 / wi), r) for wi, r in zip(w, rows)]
    keyed.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in keyed]


class Database:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        try:
            self._init()
        except BaseException:
            self.conn.close()          # a corrupt file must not stay locked (Windows cannot move an open file)
            raise

    def _init(self) -> None:
        self.conn.row_factory = sqlite3.Row
        # SQLite's NOCASE only folds ASCII; UNI folds Cyrillic and other scripts too.
        self.conn.create_collation("UNI", lambda a, b: (a.casefold() > b.casefold()) - (a.casefold() < b.casefold()))
        self.conn.execute("PRAGMA busy_timeout=30000")      # the app, scripts and worker threads share the file: wait, do not fail
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        if not self.conn.execute("SELECT 1 FROM moods").fetchone():
            for m in DEFAULT_MOODS:
                self.conn.execute("INSERT INTO moods(name) VALUES(?)", (m,))
        self._fill_default_prompts()
        self._migrate()
        from .genres import load_custom
        load_custom(self)                         # the user's own families / sub-genres join the genre tree

    def _fill_default_prompts(self) -> None:
        """Text prompts (used by CLAP zero-shot) for the built-in moods; user-edited prompts are kept."""
        from .moods import PROMPTS as DEFAULT_PROMPTS
        for name, prompt in DEFAULT_PROMPTS.items():
            self.conn.execute("UPDATE moods SET prompt=? WHERE name=? AND (prompt IS NULL OR prompt='')", (prompt, name))

    def _migrate(self) -> None:
        """v2: featured artists parsed from titles/artist tags; v3/v4: the bigger mood vocabulary (new defaults are added once)."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(tracks)")}
        for col, typ in (("lang", "TEXT"), ("lang_src", "TEXT"), ("vocal", "TEXT"), ("vocal_src", "TEXT"),
                         ("vocal_conf", "REAL"), ("flags_src", "TEXT"), ("era", "INTEGER"), ("edited", "TEXT")):   # v5: lyrics language, instrumental/vocals, flags
            if col not in cols:
                self.conn.execute(f"ALTER TABLE tracks ADD COLUMN {col} {typ}")
        if self.conn.execute("PRAGMA user_version").fetchone()[0] < 4:
            for m in DEFAULT_MOODS:                                    # existing catalogues get the new moods once
                self.conn.execute("INSERT OR IGNORE INTO moods(name) VALUES(?)", (m,))
            self._fill_default_prompts()
        if self.conn.execute("PRAGMA user_version").fetchone()[0] < 2:
            self.conn.execute("BEGIN")
            for r in self.conn.execute("SELECT id,title,artist FROM tracks").fetchall():
                self._set_feat(r["id"], r["title"], r["artist"])
                self.refresh_fts(r["id"])
            self.conn.execute("COMMIT")
        if self.conn.execute("PRAGMA user_version").fetchone()[0] < 13:
            # v6: vocabulary clean-up - renamed, merged and dropped moods keep their labels
            for old, new in MOOD_RENAMES.items():
                if self.conn.execute("SELECT 1 FROM moods WHERE name=? COLLATE UNI", (old,)).fetchone():
                    self.conn.execute("INSERT OR IGNORE INTO moods(name) VALUES(?)", (new,))
                    self.merge_mood(old, new)
            for old in MOOD_DROPPED:
                self.delete_mood(old)
            for m in DEFAULT_MOODS:
                self.conn.execute("INSERT OR IGNORE INTO moods(name) VALUES(?)", (m,))
            self._fill_default_prompts()
        if self.conn.execute("PRAGMA user_version").fetchone()[0] < 18:   # v17: Berlin School...; v18: no Russian
            # v14: one genre vocabulary (aliases merged, sub-genre -> family) - see core/genres.py; v15: new aliases;
            # v16: English names, glued tags («Post-Hardcore | Pop-Punk») split, the user's genres put in the tree
            self.conn.execute("BEGIN")
            self._translate_custom_genres()
            self.normalize_all_genres()
            self._translate_hidden_genres()
            self.conn.execute("COMMIT")
        self.conn.execute("PRAGMA user_version=18")

    def _translate_hidden_genres(self) -> None:
        """Genres a listener hid («Не показывать») under their old names follow the vocabulary («Поп» -> Pop)."""
        from .genres import canonical
        try:
            rows = self.conn.execute("SELECT user, key, added FROM hidden WHERE kind='genre'").fetchall()
        except sqlite3.OperationalError:
            return
        for user, key, added in rows:
            new = (canonical(key) or [None])[0]
            if new is None:                              # a Russian name the vocabulary does not know
                self.conn.execute("DELETE FROM hidden WHERE user=? AND kind='genre' AND key=?", (user, key))
            elif new != key:
                self.conn.execute("DELETE FROM hidden WHERE user=? AND kind='genre' AND key=?", (user, key))
                self.conn.execute("INSERT OR IGNORE INTO hidden VALUES(?,'genre',?,?)", (user, new, added))

    def _translate_custom_genres(self) -> None:
        """The user's own genres in the new vocabulary: one the tree now has («Дезкор» -> Deathcore) is dropped from
        custom_genres, the others get the family's new name («Сладж» under «Метал» -> under Metal)."""
        from .genres import GENRE_CHIPS, _reset_tree, canonical, load_custom
        try:
            rows = self.conn.execute("SELECT name, parent FROM custom_genres").fetchall()
        except sqlite3.OperationalError:
            return
        _reset_tree()                                    # the built-in tree only
        for name, parent in rows:
            c = (canonical(name) or [None])[0]
            if c is None or c in GENRE_CHIPS:            # built in now, or Russian (genres are English only)
                self.conn.execute("DELETE FROM custom_genres WHERE name=?", (name,))
            elif parent:
                self.conn.execute("UPDATE custom_genres SET parent=? WHERE name=?",
                                  ((canonical(parent) or [parent])[0], name))
        load_custom(self)

    def _set_feat(self, tid: int, title: str | None, artist: str | None) -> None:
        self.conn.execute("DELETE FROM track_artists WHERE track_id=? AND role='feat'", (tid,))
        self.conn.executemany("INSERT OR IGNORE INTO track_artists VALUES(?,?, 'feat')",
                              [(tid, n) for n in parse_feat(title, artist).feat])

    def close(self) -> None:
        self.conn.close()

    def backup(self, dest: str | Path) -> None:
        """Consistent copy of the live database (safe while the app runs)."""
        out = sqlite3.connect(str(dest))
        try:
            self.conn.backup(out)
        finally:
            out.close()

    def quick_check(self) -> bool:
        try:
            return self.conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        except sqlite3.DatabaseError:
            return False

    # ---- tracks -------------------------------------------------------
    def upsert_track(self, d: dict[str, Any], genres: list[str]) -> int:
        """Insert/update a scanned track; edited genres survive unless the file's genres changed."""
        c = self.conn
        row = c.execute("SELECT id, genres_file, edited FROM tracks WHERE path=?", (d["path"],)).fetchone()
        if row is not None and row["edited"]:                # the user's corrections win over the file tags
            d = {**d, **json.loads(row["edited"])}
        gf = "; ".join(genres)
        cols = ["mtime", "size", "title", "artist", "albumartist", "album", "year", "track_no",
                "disc_no", "duration", "cover_key"]
        vals = [d.get(k) for k in cols]
        if row is None:
            cur = c.execute(
                f"INSERT INTO tracks(path,{','.join(cols)},genres_file,added,missing) "
                f"VALUES(?,{','.join('?' * len(cols))},?,?,0)", [d["path"], *vals, gf, time.time()])
            tid = cur.lastrowid
            self._set_genres(tid, genres)
        else:
            tid = row["id"]
            c.execute(f"UPDATE tracks SET {','.join(k + '=?' for k in cols)},genres_file=?,missing=0 WHERE id=?",
                      [*vals, gf, tid])
            if row["genres_file"] != gf:
                self._set_genres(tid, genres)
        self._set_feat(tid, d.get("title"), d.get("artist"))
        self.refresh_fts(tid)
        return tid

    EDITABLE = ("title", "artist", "albumartist", "album", "year")

    def edit_track(self, tids, fields: dict) -> int:
        """Correct title / artist / album artist / album / year in the catalogue (files are not touched).

        Corrections are remembered and survive rescans.  An empty value removes the correction for that field."""
        n = 0
        for tid in tids:
            r = self.conn.execute("SELECT edited FROM tracks WHERE id=?", (tid,)).fetchone()
            if r is None:
                continue
            ed = json.loads(r[0] or "{}")
            for k, v in fields.items():
                if k not in self.EDITABLE:
                    continue
                if k == "year":
                    v = int(v) if str(v or "").strip().isdigit() else None
                else:
                    v = (v or "").strip() or None
                if v is None:
                    ed.pop(k, None)
                else:
                    ed[k] = v
                self.conn.execute(f"UPDATE tracks SET {k}=? WHERE id=?", (v, tid))
            self.conn.execute("UPDATE tracks SET edited=? WHERE id=?", (json.dumps(ed, ensure_ascii=False) if ed else None, tid))
            t = self.conn.execute("SELECT title, artist FROM tracks WHERE id=?", (tid,)).fetchone()
            self._set_feat(tid, t["title"], t["artist"])
            self.refresh_fts(tid)
            n += 1
        self.conn.commit()
        return n

    def artists_overview(self) -> list[dict]:
        """One row per artist (album artist, else artist): tracks, albums, plays, years, top genres / moods,
        how much is labelled, language and voice."""
        c = self.conn
        key = "COALESCE(NULLIF(t.albumartist,''), t.artist)"
        rows = {r["a"]: dict(r) for r in c.execute(
            f"SELECT {key} AS a, COUNT(*) AS tracks, COUNT(DISTINCT t.album) AS albums, SUM(t.play_count) AS plays, "
            f"MIN(t.year) AS y0, MAX(t.year) AS y1, MAX(t.last_played) AS last, "
            f"SUM(EXISTS(SELECT 1 FROM track_moods m WHERE m.track_id=t.id AND m.source IN ('manual','seed'))) AS labelled, "
            f"SUM(t.lang='ru') AS ru, SUM(t.vocal='inst') AS inst "
            f"FROM tracks t WHERE t.missing=0 AND {key} IS NOT NULL GROUP BY a")}
        for kind, sql in (("genres", f"SELECT {key} AS a, g.genre AS v, COUNT(*) AS n FROM track_genres g JOIN tracks t ON t.id=g.track_id "
                                     f"WHERE t.missing=0 GROUP BY a, v"),
                          ("moods", f"SELECT {key} AS a, m.name AS v, COUNT(*) AS n FROM track_moods tm JOIN moods m ON m.id=tm.mood_id "
                                    f"JOIN tracks t ON t.id=tm.track_id WHERE t.missing=0 AND tm.source IN ('manual','seed','auto') GROUP BY a, v"),
                          ("voice", f"SELECT {key} AS a, f.flag AS v, COUNT(*) AS n FROM track_flags f JOIN tracks t ON t.id=f.track_id "
                                    f"WHERE t.missing=0 AND f.flag IN ('male','female') GROUP BY a, v")):
            for r in c.execute(sql):
                if r["a"] in rows:
                    rows[r["a"]].setdefault(kind, []).append((r["n"], r["v"]))
        out = []
        for a, d in rows.items():
            for kind in ("genres", "moods", "voice"):
                d[kind] = [v for _, v in sorted(d.get(kind, []), reverse=True)]
            d["artist"] = a
            out.append(d)
        return sorted(out, key=lambda d: (-d["tracks"], d["artist"].casefold()))

    def _set_genres(self, tid: int, genres: list[str]) -> None:
        """Every genre write goes through the vocabulary (core/genres.py): canonical names, sub-genre -> its family."""
        from .genres import normalize
        self.conn.execute("DELETE FROM track_genres WHERE track_id=?", (tid,))
        self.conn.executemany("INSERT OR IGNORE INTO track_genres VALUES(?,?)", [(tid, g) for g in normalize(genres)])

    def normalize_all_genres(self) -> dict:
        """Brings every track's genres to the vocabulary (aliases in other languages merged, families added)."""
        from collections import defaultdict
        from .genres import normalize
        cur: dict[int, list[str]] = defaultdict(list)
        for tid, g in self.conn.execute("SELECT track_id, genre FROM track_genres ORDER BY rowid"):
            cur[tid].append(g)
        before = {g.casefold() for gs in cur.values() for g in gs}
        changed = 0
        for tid, gs in cur.items():
            new = normalize(gs)
            if new != gs:
                self._set_genres(tid, new)
                self.refresh_fts(tid)
                changed += 1
        after = {g.casefold() for (g,) in self.conn.execute("SELECT DISTINCT genre FROM track_genres")}
        return {"tracks_changed": changed, "genres_before": len(before), "genres_after": len(after)}

    def refresh_fts(self, tid: int) -> None:
        c = self.conn
        t = c.execute("SELECT title,artist,albumartist,album,year FROM tracks WHERE id=?", (tid,)).fetchone()
        c.execute("DELETE FROM fts WHERE rowid=?", (tid,))
        if t is None:
            return
        from .genres import search_text
        genres = search_text([r[0] for r in c.execute("SELECT genre FROM track_genres WHERE track_id=?", (tid,))])
        moods = " ".join(r[0] for r in c.execute(
            "SELECT m.name FROM track_moods tm JOIN moods m ON m.id=tm.mood_id "
            f"WHERE tm.track_id=? AND tm.source IN {_LS}", (tid,)))
        feats = " ".join(r[0] for r in c.execute("SELECT name FROM track_artists WHERE track_id=? AND role='feat'", (tid,)))
        c.execute("INSERT INTO fts(rowid,title,artist,album,genres,moods,year) VALUES(?,?,?,?,?,?,?)",
                  (tid, t["title"] or "", f"{t['artist'] or ''} {t['albumartist'] or ''} {feats}",
                   t["album"] or "", genres, moods, str(t["year"] or "")))

    def known_files(self) -> dict[str, tuple[float, int]]:
        return {r["path"]: (r["mtime"], r["size"]) for r in self.conn.execute("SELECT path,mtime,size FROM tracks")}

    def mark_missing(self, paths: Iterable[str]) -> None:
        self.conn.executemany("UPDATE tracks SET missing=1 WHERE path=?", [(p,) for p in paths])

    def get_track(self, tid: int) -> dict | None:
        r = self.conn.execute(f"SELECT {TRACK_COLS} FROM tracks WHERE id=?", (tid,)).fetchone()
        return self._enrich([dict(r)])[0] if r else None

    def _enrich(self, rows: list[dict]) -> list[dict]:
        """Add genres, confirmed moods, mood suggestions and per-period play counts."""
        if not rows:
            return rows
        ids = [r["id"] for r in rows]
        q = ",".join("?" * len(ids))
        g: dict[int, list[str]] = {}
        for r in self.conn.execute(f"SELECT track_id,genre FROM track_genres WHERE track_id IN ({q}) ORDER BY rowid", ids):
            g.setdefault(r[0], []).append(r[1])
        m: dict[int, list[str]] = {}
        s: dict[int, list[dict]] = {}
        src: dict[int, dict[str, str]] = {}
        conf: dict[int, dict[str, float]] = {}
        for r in self.conn.execute(
                f"SELECT tm.track_id,m.name,tm.source,tm.confidence FROM track_moods tm "
                f"JOIN moods m ON m.id=tm.mood_id WHERE tm.track_id IN ({q}) ORDER BY m.name", ids):
            if r[2] in LABEL_SOURCES:
                m.setdefault(r[0], []).append(r[1])
                src.setdefault(r[0], {})[r[1]] = r[2]
                conf.setdefault(r[0], {})[r[1]] = r[3]
            else:
                s.setdefault(r[0], []).append({"mood": r[1], "source": r[2], "confidence": r[3]})
        now = time.time()
        per = {k: {} for k in PERIODS}
        for k, days in PERIODS.items():
            for r in self.conn.execute(
                    f"SELECT track_id,COUNT(*) FROM plays WHERE played_at>=? AND track_id IN ({q}) GROUP BY track_id",
                    [now - days * 86400, *ids]):
                per[k][r[0]] = r[1]
        f: dict[int, list[str]] = {}
        for r in self.conn.execute(
                f"SELECT track_id,name FROM track_artists WHERE role='feat' AND track_id IN ({q}) ORDER BY rowid", ids):
            f.setdefault(r[0], []).append(r[1])
        fl: dict[int, list[str]] = {}
        for r in self.conn.execute(f"SELECT track_id,flag FROM track_flags WHERE track_id IN ({q}) ORDER BY flag", ids):
            fl.setdefault(r[0], []).append(r[1])
        for r in rows:
            r["flags"] = fl.get(r["id"], [])
            r["feat"] = f.get(r["id"], [])
            r["genres"] = g.get(r["id"], [])
            r["moods"] = m.get(r["id"], [])
            r["mood_suggestions"] = s.get(r["id"], [])
            r["mood_sources"] = src.get(r["id"], {})
            r["mood_conf"] = conf.get(r["id"], {})
            r["plays_by_period"] = {k: per[k].get(r["id"], 0) for k in PERIODS}
        return rows

    # ---- search / filter ---------------------------------------------
    def search(self, text: str = "", *, genre: str | None = None, mood: str | None = None,
               mood_status: str = "confirmed", no_mood: bool = False, artist: str | None = None,
               feat: str | None = None, album: str | None = None, year_from: int | None = None, year_to: int | None = None,
               min_plays: int | None = None, max_plays: int | None = None, plays_days: int | None = None,
               not_played_days: int | None = None, order: str = "artist", desc: bool = False,
               rare_power: float = 1.0, limit: int = 200, offset: int = 0, lang: str | None = None,
               vocal: str | None = None, flag: str | None = None, exclude_flags: Iterable[str] | None = None,
               hide_user: str | None = None) -> list[dict]:
        """Filter tracks.  ``plays_days`` restricts min/max_plays and order='plays_period' to the last N days.
        ``hide_user``: leave out what that listener chose «Не показывать» (core/hidden.py).

        order='rare_random' is a weighted shuffle preferring rarely played tracks.
        """
        where: list[str] = ["t.missing=0"]
        args: list[Any] = []
        if hide_user:
            from .hidden import SQL_VISIBLE
            where.append(SQL_VISIBLE)
            args.append(hide_user)
        pcount = "t.play_count"
        if plays_days is not None:
            pcount = "(SELECT COUNT(*) FROM plays p WHERE p.track_id=t.id AND p.played_at>=?)"
        if text.strip():
            terms = re.findall(r"\w+", text, re.UNICODE)
            if terms:
                where.append("t.id IN (SELECT rowid FROM fts WHERE fts MATCH ?)")
                args.append(" ".join(f'"{w}"*' for w in terms))
        if genre:
            from .genres import canonical          # «Рок» / «Rap» from an older client mean «Rock» / «Hip-Hop»
            where.append("EXISTS(SELECT 1 FROM track_genres g WHERE g.track_id=t.id AND g.genre=? COLLATE UNI)")
            args.append((canonical(genre) or [genre])[0])
        if mood:
            st = "" if mood_status == "any" else (
                f"AND tm.source IN {_LS}" if mood_status == "confirmed" else f"AND tm.source NOT IN {_LS}")
            where.append("EXISTS(SELECT 1 FROM track_moods tm JOIN moods m ON m.id=tm.mood_id "
                         f"WHERE tm.track_id=t.id AND m.name=? COLLATE UNI {st})")
            args.append(mood)
        if no_mood:
            where.append(f"NOT EXISTS(SELECT 1 FROM track_moods tm WHERE tm.track_id=t.id AND tm.source IN {_LS})")
        if artist:
            where.append("(t.artist=? COLLATE UNI OR t.albumartist=? COLLATE UNI OR EXISTS(SELECT 1 FROM "
                         "track_artists a WHERE a.track_id=t.id AND a.name=? COLLATE UNI))")
            args += [artist, artist, artist]
        if feat:
            where.append("EXISTS(SELECT 1 FROM track_artists a WHERE a.track_id=t.id AND a.role='feat' "
                         "AND a.name=? COLLATE UNI)")
            args.append(feat)
        if album:
            where.append("t.album=? COLLATE UNI")
            args.append(album)
        if flag:
            where.append("EXISTS(SELECT 1 FROM track_flags f WHERE f.track_id=t.id AND f.flag=?)"); args.append(flag)
        for ef in exclude_flags or []:
            where.append("NOT EXISTS(SELECT 1 FROM track_flags f WHERE f.track_id=t.id AND f.flag=?)"); args.append(ef)
        if lang:
            if lang == "foreign":                      # everything that is not Russian
                where.append("(t.lang IS NULL OR t.lang<>'ru')")
            else:
                where.append("t.lang=?"); args.append(lang)
        if vocal:
            where.append("t.vocal=?"); args.append(vocal)
        if year_from is not None:
            where.append("COALESCE(t.year, t.era+5)>=?"); args.append(year_from)
        if year_to is not None:
            where.append("COALESCE(t.year, t.era+5)<=?"); args.append(year_to)
        pargs: list[Any] = [time.time() - plays_days * 86400] if plays_days is not None else []
        if min_plays is not None:
            where.append(f"{pcount}>=?"); args += pargs + [min_plays]
        if max_plays is not None:
            where.append(f"{pcount}<=?"); args += pargs + [max_plays]
        if not_played_days is not None:
            where.append("(t.last_played IS NULL OR t.last_played<?)")
            args.append(time.time() - not_played_days * 86400)
        cols = {"artist": "t.artist COLLATE UNI, t.album COLLATE UNI, t.disc_no, t.track_no",
                "title": "t.title COLLATE UNI", "album": "t.album COLLATE UNI, t.disc_no, t.track_no",
                "year": "t.year", "play_count": "t.play_count", "last_played": "t.last_played",
                "added": "t.added", "duration": "t.duration", "random": "RANDOM()", "rare_random": "RANDOM()"}
        order_args: list[Any] = []
        if order == "plays_period" and plays_days is not None:
            o, order_args = pcount, list(pargs)
        else:
            o = cols.get(order, cols["artist"])
        if desc and order not in ("random", "rare_random"):
            o += " DESC"
        base = (f"SELECT {', '.join('t.' + c for c in TRACK_COLS.split(','))} FROM tracks t "
                f"WHERE {' AND '.join(where)} ORDER BY {o}")
        if order == "rare_random":
            rows = [dict(r) for r in self.conn.execute(base, args)]
            rows = weighted_order(rows, rare_power)[offset:offset + limit]
        else:
            rows = [dict(r) for r in self.conn.execute(
                base + " LIMIT ? OFFSET ?", [*args, *order_args, limit, offset])]
        return self._enrich(rows)

    def count(self, **kw: Any) -> int:
        kw = {**kw, "limit": 10**9, "offset": 0}
        kw.pop("order", None)
        return len(self.search(**kw))

    def list_values(self, kind: str) -> list[dict]:
        """Group counts: kind is genre | artist | album | year."""
        q = {
            "genre": "SELECT g.genre AS name, COUNT(*) AS n FROM track_genres g JOIN tracks t ON t.id=g.track_id "
                     "WHERE t.missing=0 GROUP BY g.genre COLLATE UNI ORDER BY n DESC",
            "artist": "SELECT COALESCE(NULLIF(albumartist,''),artist) AS name, COUNT(*) AS n FROM tracks "
                      "WHERE missing=0 GROUP BY name COLLATE UNI ORDER BY name COLLATE UNI",
            "album": "SELECT album AS name, COALESCE(NULLIF(albumartist,''),artist) AS artist, MAX(year) AS year, "
                     "MAX(cover_key) AS cover_key, COUNT(*) AS n FROM tracks WHERE missing=0 AND album<>'' "
                     "GROUP BY album, artist ORDER BY artist COLLATE UNI, year",
            "feat_artist": "SELECT a.name AS name, COUNT(*) AS n FROM track_artists a JOIN tracks t ON t.id=a.track_id "
                           "WHERE t.missing=0 AND a.role='feat' GROUP BY a.name COLLATE UNI ORDER BY n DESC, a.name",
            "year": "SELECT year AS name, COUNT(*) AS n FROM tracks WHERE missing=0 AND year IS NOT NULL "
                    "GROUP BY year ORDER BY year",
        }[kind]
        return [dict(r) for r in self.conn.execute(q)]

    # ---- language / vocals ---------------------------------------------
    def set_language(self, tids: Iterable[int], lang: str | None, src: str = "manual", only_auto: bool = False) -> int:
        """Set the lyrics language.  only_auto: leave values set by the user (src manual/seed) untouched."""
        n = 0
        for tid in tids:
            cond = " AND (lang_src IS NULL OR lang_src='auto')" if only_auto else ""
            n += self.conn.execute(f"UPDATE tracks SET lang=?, lang_src=? WHERE id=?{cond}", (lang, src if lang else None, tid)).rowcount
        return n

    def set_flags(self, tids: Iterable[int], flags: Iterable[str], mode: str = "set", src: str = "manual") -> None:
        """Recording-type flags (live / acoustic / demo / remix / cover).  mode: set | add | remove.

        A manual edit freezes the track's flags: automatic detection no longer rewrites them."""
        flags = list(flags)
        for tid in tids:
            if src == "auto":
                cur = self.conn.execute("SELECT flags_src FROM tracks WHERE id=?", (tid,)).fetchone()
                if cur and cur[0] == "manual":
                    continue
            if mode == "set":
                self.conn.execute("DELETE FROM track_flags WHERE track_id=?", (tid,))
            for f in flags:
                if mode == "remove":
                    self.conn.execute("DELETE FROM track_flags WHERE track_id=? AND flag=?", (tid, f))
                else:
                    self.conn.execute("INSERT OR IGNORE INTO track_flags VALUES(?,?)", (tid, f))
            self.conn.execute("UPDATE tracks SET flags_src=? WHERE id=?", (src, tid))

    def set_era(self, tids: Iterable[int], decade: int | None) -> int:
        """Decade (1980, 2000, ...) for tracks whose file has no year; the real year, when known, wins in filters."""
        return sum(self.conn.execute("UPDATE tracks SET era=? WHERE id=?", (decade, tid)).rowcount for tid in tids)

    def set_vocal(self, tids: Iterable[int], vocal: str | None, src: str = "manual", only_auto: bool = False,
                  confidence: float | None = None) -> int:
        """vocal: 'inst' (instrumental) | 'voc' (has vocals) | None."""
        n = 0
        for tid in tids:
            cond = " AND (vocal_src IS NULL OR vocal_src='auto')" if only_auto else ""
            n += self.conn.execute(f"UPDATE tracks SET vocal=?, vocal_src=?, vocal_conf=? WHERE id=?{cond}",
                                   (vocal, src if vocal else None, confidence, tid)).rowcount
        return n

    # ---- genres -------------------------------------------------------
    def set_genres(self, tids: Iterable[int], genres: list[str], mode: str = "set") -> None:
        """mode: set | add | remove.  Names in any language/spelling are understood (core/genres.py); removing a
        family removes its sub-genres too (a sub-genre always keeps its family)."""
        from .genres import GENRE_TREE, canonical
        genres = split_genres(genres)
        if mode == "remove":
            genres = [c for g in genres for c in (canonical(g) or [g])]
            genres += [s for g in list(genres) for s in GENRE_TREE.get(g, [])]
        for tid in tids:
            cur = [r[0] for r in self.conn.execute("SELECT genre FROM track_genres WHERE track_id=?", (tid,))]
            if mode == "add":
                new = cur + [g for g in genres if g.lower() not in (x.lower() for x in cur)]
            elif mode == "remove":
                new = [g for g in cur if g.lower() not in (x.lower() for x in genres)]
            else:
                new = genres
            self._set_genres(tid, new)
            self.refresh_fts(tid)

    def genre_changes(self) -> list[dict]:
        """Tracks whose DB genres differ from the file tag (candidates for writing to files)."""
        out = []
        for r in self.conn.execute("SELECT id,path,genres_file FROM tracks WHERE missing=0"):
            cur = [x[0] for x in self.conn.execute(
                "SELECT genre FROM track_genres WHERE track_id=? ORDER BY rowid", (r["id"],))]
            if [g.lower() for g in cur] != [g.lower() for g in split_genres(r["genres_file"])]:
                out.append({"id": r["id"], "path": r["path"], "file": split_genres(r["genres_file"]), "db": cur})
        return out

    # ---- moods --------------------------------------------------------
    def list_moods(self) -> list[dict]:
        """Moods in vocabulary order (see moods.py), the user's own ones last."""
        from .moods import ordered
        rows = self._list_moods_raw()
        rank = {n: i for i, n in enumerate(ordered([r["name"] for r in rows]))}
        return sorted(rows, key=lambda r: rank[r["name"]])

    def _list_moods_raw(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT m.name, m.prompt, "
            f"SUM(CASE WHEN t.source IN {_LS} THEN 1 ELSE 0 END) AS n, "
            f"SUM(CASE WHEN t.source NOT IN {_LS} THEN 1 ELSE 0 END) AS suggested "
            "FROM moods m LEFT JOIN track_moods t ON t.mood_id=m.id GROUP BY m.id ORDER BY m.name")]

    def add_mood(self, name: str, prompt: str | None = None) -> int:
        name = name.strip()
        self.conn.execute("INSERT OR IGNORE INTO moods(name) VALUES(?)", (name,))
        mid = self.conn.execute("SELECT id FROM moods WHERE name=? COLLATE UNI", (name,)).fetchone()[0]
        if prompt is not None:
            self.conn.execute("UPDATE moods SET prompt=? WHERE id=?", (prompt, mid))
        return mid

    def rename_mood(self, old: str, new: str) -> None:
        self.conn.execute("UPDATE moods SET name=? WHERE name=? COLLATE UNI", (new.strip(), old))
        for (tid,) in self.conn.execute("SELECT DISTINCT track_id FROM track_moods").fetchall():
            self.refresh_fts(tid)

    def mood_example_counts(self) -> dict[str, int]:
        """{mood: tracks the model learns it from} (labels set by the user or the seed set)."""
        return {r[0]: r[1] for r in self.conn.execute(
            "SELECT m.name, COUNT(tm.track_id) FROM moods m LEFT JOIN track_moods tm ON tm.mood_id=m.id "
            "AND tm.source IN ('manual','seed') GROUP BY m.id")}

    def merge_mood(self, old: str, new: str) -> None:
        """Fold mood ``old`` into ``new`` (labels, suggestions and rejects move over), then drop ``old``."""
        a = self.conn.execute("SELECT id FROM moods WHERE name=? COLLATE UNI", (old,)).fetchone()
        b = self.conn.execute("SELECT id FROM moods WHERE name=? COLLATE UNI", (new,)).fetchone()
        if not a or not b:
            return
        self.conn.execute("INSERT OR IGNORE INTO track_moods(track_id,mood_id,source,confidence) "
                          "SELECT track_id,?,source,confidence FROM track_moods WHERE mood_id=?", (b[0], a[0]))
        self.conn.execute("INSERT OR IGNORE INTO mood_rejects(track_id,mood_id) SELECT track_id,? FROM mood_rejects WHERE mood_id=?",
                          (b[0], a[0]))
        self.conn.execute("DELETE FROM mood_rejects WHERE mood_id=?", (a[0],))
        self.delete_mood(old)
        for (tid,) in self.conn.execute("SELECT DISTINCT track_id FROM track_moods WHERE mood_id=?", (b[0],)).fetchall():
            self.refresh_fts(tid)

    def delete_mood(self, name: str) -> None:
        mid = self.conn.execute("SELECT id FROM moods WHERE name=? COLLATE UNI", (name,)).fetchone()
        if mid:
            self.conn.execute("DELETE FROM track_moods WHERE mood_id=?", (mid[0],))
            self.conn.execute("DELETE FROM moods WHERE id=?", (mid[0],))

    def set_moods(self, tids: Iterable[int], moods: list[str], mode: str = "set") -> None:
        """Confirmed (manual) moods. mode: set | add | remove.  A manual label replaces a suggestion."""
        ids = [self.add_mood(m) for m in moods if m.strip()]
        for tid in tids:
            if mode == "set":
                self.conn.execute(f"DELETE FROM track_moods WHERE track_id=? AND source IN {_LS}", (tid,))
            for mid in ids:
                if mode == "remove":
                    self.conn.execute("DELETE FROM track_moods WHERE track_id=? AND mood_id=?", (tid, mid))
                else:
                    self.conn.execute("INSERT OR REPLACE INTO track_moods VALUES(?,?,?,NULL)", (tid, mid, CONFIRMED))
                    self.conn.execute("DELETE FROM mood_rejects WHERE track_id=? AND mood_id=?", (tid, mid))
            self.refresh_fts(tid)

    def suggest_moods(self, items: Iterable[tuple[int, str, float | None]], source: str = "claude") -> int:
        """Store suggestions (track_id, mood, confidence); never overrides a confirmed label."""
        n = 0
        for tid, mood, conf in items:
            mood = canonical_mood(mood)
            if mood is None:
                continue                       # a mood that was removed from the vocabulary
            mid = self.add_mood(mood)
            cur = self.conn.execute("SELECT source FROM track_moods WHERE track_id=? AND mood_id=?", (tid, mid)).fetchone()
            if cur and cur[0] in TRAIN_SOURCES:
                continue                       # never overwrite a user's / seed label
            if source == "auto" and self.conn.execute(
                    f"SELECT 1 FROM track_moods WHERE track_id=? AND source IN {_TS}", (tid,)).fetchone():
                continue                       # a track the user/seed labelled keeps exactly those moods
            if self.conn.execute("SELECT 1 FROM mood_rejects WHERE track_id=? AND mood_id=?", (tid, mid)).fetchone():
                continue  # the user said no to this pair: never offer it again
            self.conn.execute("INSERT OR REPLACE INTO track_moods VALUES(?,?,?,?)", (tid, mid, source, conf))
            n += 1
        return n

    def clear_suggestions(self, source: str | None = None) -> None:
        if source:
            self.conn.execute("DELETE FROM track_moods WHERE source=?", (source,))
        else:
            self.conn.execute(f"DELETE FROM track_moods WHERE source NOT IN {_LS}")

    def resolve_suggestions(self, tids: Iterable[int], accept: bool = True, moods: list[str] | None = None,
                            min_confidence: float | None = None) -> int:
        """Accept (-> confirmed) or reject (delete) suggestions for tracks, optionally for given moods only."""
        n = 0
        for tid in tids:
            rows = self.conn.execute(
                "SELECT tm.mood_id,m.name,tm.confidence FROM track_moods tm JOIN moods m ON m.id=tm.mood_id "
                f"WHERE tm.track_id=? AND tm.source NOT IN {_TS}", (tid,)).fetchall()
            for mid, name, conf in rows:
                if moods and name.lower() not in (x.lower() for x in moods):
                    continue
                if accept and min_confidence is not None and (conf or 0) < min_confidence:
                    continue
                if accept:
                    self.conn.execute("UPDATE track_moods SET source='manual',confidence=NULL "
                                      "WHERE track_id=? AND mood_id=?", (tid, mid))
                else:
                    self.conn.execute("DELETE FROM track_moods WHERE track_id=? AND mood_id=?", (tid, mid))
                    self.conn.execute("INSERT OR REPLACE INTO mood_rejects VALUES(?,?,?)", (tid, mid, time.time()))
                n += 1
            self.refresh_fts(tid)
        return n

    def unlabelled_ids(self) -> list[int]:
        """Tracks without a manual/seed label (they may carry an auto label, which a retrained model replaces)."""
        return [r[0] for r in self.conn.execute(
            f"SELECT t.id FROM tracks t WHERE t.missing=0 AND NOT EXISTS(SELECT 1 FROM track_moods m "
            f"WHERE m.track_id=t.id AND m.source IN {_TS})")]

    # ---- favourites (kind: track -> str(id) | album -> "artist\x1falbum" | artist -> name) ----------------
    def set_favorite(self, kind: str, key: str, on: bool = True) -> None:
        if on:
            self.conn.execute("INSERT OR IGNORE INTO favorites VALUES(?,?,?)", (kind, key, time.time()))
        else:
            self.conn.execute("DELETE FROM favorites WHERE kind=? AND key=?", (kind, key))
        self.conn.commit()

    def is_favorite(self, kind: str, key: str) -> bool:
        return self.conn.execute("SELECT 1 FROM favorites WHERE kind=? AND key=?", (kind, key)).fetchone() is not None

    def favorites(self, kind: str) -> list[str]:
        """Keys of one kind, newest first."""
        return [r[0] for r in self.conn.execute("SELECT key FROM favorites WHERE kind=? ORDER BY added DESC", (kind,))]

    def reject_moods(self, tid: int, moods: list[str]) -> None:
        """The user says these moods do NOT fit the track (negative examples; the model will not offer them again)."""
        for m in moods:
            mid = self.add_mood(m)
            self.conn.execute("DELETE FROM track_moods WHERE track_id=? AND mood_id=? AND source NOT IN ('manual','seed')",
                              (tid, mid))
            self.conn.execute("INSERT OR REPLACE INTO mood_rejects VALUES(?,?,?)", (tid, mid, __import__("time").time()))
        self.refresh_fts(tid)

    def mood_negatives(self) -> dict[int, set[str]]:
        """Moods the user explicitly rejected for a track (suggestion -> reject): negative training examples."""
        out: dict[int, set[str]] = {}
        for r in self.conn.execute("SELECT r.track_id,m.name FROM mood_rejects r JOIN moods m ON m.id=r.mood_id "
                                   "JOIN tracks t ON t.id=r.track_id WHERE t.missing=0"):
            out.setdefault(r[0], set()).add(r[1])
        return out

    def mood_training_set(self) -> list[tuple[int, list[str]]]:
        """Confirmed labels only: [(track_id, [mood, ...])]."""
        out: dict[int, list[str]] = {}
        for r in self.conn.execute("SELECT tm.track_id,m.name FROM track_moods tm JOIN moods m ON m.id=tm.mood_id "
                                   f"JOIN tracks t ON t.id=tm.track_id WHERE tm.source IN {_TS} AND t.missing=0"):
            out.setdefault(r[0], []).append(r[1])
        return list(out.items())

    # ---- descriptors / embeddings (inputs of the mood model) ----------
    def set_descriptors(self, tid: int, data: dict, version: int = 1) -> None:
        self.conn.execute("INSERT OR REPLACE INTO track_descriptors VALUES(?,?,?)",
                          (tid, json.dumps(data, ensure_ascii=False), version))

    def get_descriptors(self, tid: int) -> dict | None:
        r = self.conn.execute("SELECT data FROM track_descriptors WHERE track_id=?", (tid,)).fetchone()
        return json.loads(r[0]) if r else None

    def tracks_without_descriptors(self, limit: int = 100000) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT t.id,t.path FROM tracks t LEFT JOIN track_descriptors d ON d.track_id=t.id "
            "WHERE t.missing=0 AND d.track_id IS NULL LIMIT ?", (limit,))]

    def set_embedding(self, tid: int, model: str, blob: bytes) -> None:
        self.conn.execute("INSERT OR REPLACE INTO track_embeddings VALUES(?,?,?)", (tid, model, blob))

    def get_embedding(self, tid: int, model: str) -> bytes | None:
        r = self.conn.execute("SELECT vec FROM track_embeddings WHERE track_id=? AND model=?", (tid, model)).fetchone()
        return r[0] if r else None

    # ---- plays --------------------------------------------------------
    def record_play(self, tid: int, listened: float = 0.0, when: float | None = None) -> None:
        when = when if when is not None else time.time()
        self.conn.execute("INSERT INTO plays(track_id,played_at,listened) VALUES(?,?,?)", (tid, when, listened))
        self.conn.execute("UPDATE tracks SET play_count=play_count+1,"
                          "last_played=MAX(COALESCE(last_played,0),?) WHERE id=?", (when, tid))

    def stats(self, top: int = 20, period_days: int | None = None, stale_days: int = 90) -> dict:
        """Listening statistics; ``period_days`` limits most-played/top lists to that window (month=30, year=365)."""
        c = self.conn
        since = time.time() - period_days * 86400 if period_days else 0
        most = [dict(r) for r in c.execute(
            "SELECT track_id AS id, COUNT(*) AS plays FROM plays WHERE played_at>=? GROUP BY track_id "
            "ORDER BY plays DESC, MAX(played_at) DESC LIMIT ?", (since, top))]
        for m in most:
            t = self.get_track(m["id"])
            if t:
                m.update(t | {"plays": m["plays"]})
        sel = f"SELECT {TRACK_COLS} FROM tracks WHERE missing=0 "
        stale = self._enrich([dict(r) for r in c.execute(
            sel + "AND play_count>0 AND last_played<? ORDER BY last_played LIMIT ?",
            (time.time() - stale_days * 86400, top))])
        never = c.execute("SELECT COUNT(*) FROM tracks WHERE missing=0 AND play_count=0").fetchone()[0]
        total = c.execute("SELECT COUNT(*) FROM tracks WHERE missing=0").fetchone()[0]
        artists = [dict(r) for r in c.execute(
            "SELECT COALESCE(NULLIF(t.albumartist,''),t.artist) AS artist, COUNT(*) AS plays FROM plays p "
            "JOIN tracks t ON t.id=p.track_id WHERE p.played_at>=? GROUP BY artist ORDER BY plays DESC LIMIT ?",
            (since, top))]
        genres = [dict(r) for r in c.execute(
            "SELECT g.genre, COUNT(*) AS plays FROM plays p JOIN track_genres g ON g.track_id=p.track_id "
            "WHERE p.played_at>=? GROUP BY g.genre COLLATE UNI ORDER BY plays DESC LIMIT ?", (since, top))]
        totals = {k: c.execute("SELECT COUNT(*) FROM plays WHERE played_at>=?",
                               (time.time() - d * 86400,)).fetchone()[0] for k, d in PERIODS.items()}
        return {"total": total, "never_played": never, "plays_total_by_period": totals,
                "period_days": period_days, "most_played": most, "not_played_recently": stale,
                "top_artists": artists, "top_genres": genres}

    # ---- playlists ----------------------------------------------------
    def create_playlist(self, name: str, rules: dict | None = None) -> int:
        cur = self.conn.execute("INSERT INTO playlists(name,rules) VALUES(?,?)",
                                (name, json.dumps(rules, ensure_ascii=False) if rules else None))
        return cur.lastrowid

    def list_playlists(self) -> list[dict]:
        out = []
        for r in self.conn.execute("SELECT id,name,rules FROM playlists ORDER BY name COLLATE UNI"):
            d = {"id": r["id"], "name": r["name"], "rules": json.loads(r["rules"]) if r["rules"] else None}
            d["count"] = (self.count(**d["rules"]) if d["rules"] else
                          self.conn.execute("SELECT COUNT(*) FROM playlist_tracks WHERE playlist_id=?",
                                            (r["id"],)).fetchone()[0])
            out.append(d)
        return out

    def playlist_row(self, pid: int) -> dict | None:
        r = self.conn.execute("SELECT id,name,rules FROM playlists WHERE id=?", (pid,)).fetchone()
        return ({"id": r["id"], "name": r["name"], "rules": json.loads(r["rules"]) if r["rules"] else None}
                if r else None)

    def playlist_tracks(self, pid: int) -> list[dict]:
        p = self.playlist_row(pid)
        if p is None:
            return []
        if p["rules"]:
            return self.search(**p["rules"])
        rows = [dict(r) for r in self.conn.execute(
            f"SELECT {', '.join('t.' + c for c in TRACK_COLS.split(','))} FROM playlist_tracks pt "
            "JOIN tracks t ON t.id=pt.track_id WHERE pt.playlist_id=? AND t.missing=0 ORDER BY pt.pos", (pid,))]
        return self._enrich(rows)

    def playlist_add(self, pid: int, tids: Iterable[int]) -> None:
        pos = self.conn.execute("SELECT COALESCE(MAX(pos),-1)+1 FROM playlist_tracks WHERE playlist_id=?",
                                (pid,)).fetchone()[0]
        for t in tids:
            self.conn.execute("INSERT INTO playlist_tracks VALUES(?,?,?)", (pid, pos, t))
            pos += 1

    def playlist_remove(self, pid: int, tids: Iterable[int]) -> None:
        self.conn.executemany("DELETE FROM playlist_tracks WHERE playlist_id=? AND track_id=?",
                              [(pid, t) for t in tids])

    def update_playlist(self, pid: int, name: str | None = None, rules: dict | None = None) -> None:
        if name:
            self.conn.execute("UPDATE playlists SET name=? WHERE id=?", (name, pid))
        if rules is not None:
            self.conn.execute("UPDATE playlists SET rules=? WHERE id=?", (json.dumps(rules, ensure_ascii=False), pid))

    def delete_playlist(self, pid: int) -> None:
        self.conn.execute("DELETE FROM playlist_tracks WHERE playlist_id=?", (pid,))
        self.conn.execute("DELETE FROM playlists WHERE id=?", (pid,))
