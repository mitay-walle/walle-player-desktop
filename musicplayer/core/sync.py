"""Two-way sync of the catalogue between the library server (home server / mini PC) and desktop copies.

Every database that takes part keeps a change journal, ``sync_log``, written by SQLite triggers - so every writer is
covered (the window, job processes, the server, MCP, scripts) without touching their code.  A peer sends the entries
it made itself and takes the server's (``/api/sync/push`` / ``/api/sync/pull``); an entry is applied unless the
receiving side already has a newer one for the same thing (last writer wins: per column for tracks, per row for the
rest).  Applied entries are journaled with their origin, so they are not sent back to where they came from, and the
server passes them on to other peers.

What is shared: track tags and labels (genres, flags, moods, rejected moods, language / vocal / era, the user's
corrections, whether the server still has the file), likes and listeners' likes, hidden things, delete marks, own
genres, plays, playlists, audio descriptors and embeddings, the sound-quality check.  What is not: where the file lies on each computer (path,
mtime, size), derived
tables (the search index, featured artists - rebuilt after applying), web-lookup caches.  Rows whose id is local
(moods, plays, playlists) travel by a natural key (the mood's name, track + time, the playlist's name).  New tracks
are made only by the server's scans; a desktop copy maps the server's path to its own copy of the music folder.
"""
from __future__ import annotations

import json
import secrets
import time
from typing import Callable, Iterable

TRIGGER_VERSION = "2"                                    # 2: track_quality
LOCAL_TRACK_COLS = {"id", "path", "mtime", "size"}       # where the file lies on each computer

# table -> key columns (natural), and how the row travels
ROWS: dict[str, dict] = {
    "track_genres": {"key": ["track_id", "genre"]},
    "track_flags": {"key": ["track_id", "flag"]},
    "track_moods": {"key": ["track_id", "mood_id"], "mood": "mood_id"},
    "mood_rejects": {"key": ["track_id", "mood_id"], "mood": "mood_id"},
    "moods": {"key": ["name"], "local": {"id"}},
    "favorites": {"key": ["kind", "key"]},
    "remote_favorites": {"key": ["user", "kind", "key"]},
    "hidden": {"key": ["user", "kind", "key"]},
    "delete_marks": {"key": ["kind", "key", "user"]},
    "custom_genres": {"key": ["name"]},
    "plays": {"key": ["track_id", "played_at"], "local": {"id"}},
    "remote_plays": {"key": ["user", "track_id", "played_at"]},
    "playlists": {"key": ["name"], "local": {"id"}},
    "track_descriptors": {"key": ["track_id"]},
    "track_embeddings": {"key": ["track_id", "model"], "blob": {"vec"}},
    "track_quality": {"key": ["track_id"]},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_log(seq INTEGER PRIMARY KEY AUTOINCREMENT, tbl TEXT NOT NULL, k TEXT NOT NULL,
  col TEXT, op TEXT NOT NULL, data TEXT, at REAL NOT NULL, origin TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_sync_log_key ON sync_log(tbl, k, col, at);
CREATE INDEX IF NOT EXISTS ix_sync_log_origin ON sync_log(origin, seq);
CREATE TABLE IF NOT EXISTS sync_meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sync_flag(applying INTEGER NOT NULL);
"""
NOW = "((julianday('now') - 2440587.5) * 86400.0)"
NODE = "(SELECT value FROM sync_meta WHERE key='node')"
ON = "(SELECT applying FROM sync_flag) = 0"


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _cols(conn, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({_q(table)})")]


_COLS: dict[tuple[int, str], list[str]] = {}


def _ccols(conn, table: str) -> list[str]:
    """Columns, remembered per connection (applying thousands of entries)."""
    key = (id(conn), table)
    if key not in _COLS:
        _COLS[key] = _cols(conn, table)
    return _COLS[key]


def _table_exists(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _ensure_side_tables(conn) -> None:
    """Tables other modules create on first use: the triggers need them to exist."""
    from . import deletemarks, hidden, likes, quality
    conn.execute(likes.GUEST_TABLE)
    conn.execute(quality.SCHEMA)
    conn.execute(hidden.SCHEMA)
    conn.execute(deletemarks.SCHEMA)
    conn.execute("CREATE TABLE IF NOT EXISTS remote_plays(user TEXT, track_id INTEGER, played_at REAL, listened REAL)")


def enabled(conn) -> bool:
    return _table_exists(conn, "sync_meta") and conn.execute(
        "SELECT value FROM sync_meta WHERE key='triggers'").fetchone() is not None


def node(conn) -> str:
    return conn.execute("SELECT value FROM sync_meta WHERE key='node'").fetchone()[0]


def meta(conn, key: str, default: str | None = None) -> str | None:
    r = conn.execute("SELECT value FROM sync_meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def set_meta(conn, key: str, value) -> None:
    conn.execute("INSERT OR REPLACE INTO sync_meta VALUES(?,?)", (key, str(value)))


def enable(conn) -> str:
    """Start journaling changes in this database (idempotent; triggers are rebuilt when their version changes).
    Returns this database's node id."""
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM sync_flag").fetchone()[0] == 0:
        conn.execute("INSERT INTO sync_flag VALUES(0)")
    here = (conn.execute("PRAGMA database_list").fetchone() or [None, None, ""])[2] or ""
    if meta(conn, "node") is None:
        set_meta(conn, "node", secrets.token_hex(8))
        set_meta(conn, "file", here)
    elif meta(conn, "file") != here:
        # a copy of a journaling catalogue (a restored backup, a desktop copy of the server's): a node of its own,
        # the copied history is not sent anywhere, and whatever the server has comes again (applying is idempotent)
        set_meta(conn, "node", secrets.token_hex(8))
        set_meta(conn, "file", here)
        set_meta(conn, "pushed", head(conn))
        conn.execute("DELETE FROM sync_meta WHERE key='pulled'")
    if meta(conn, "triggers") != TRIGGER_VERSION:
        _ensure_side_tables(conn)
        _drop_triggers(conn)
        for sql in _trigger_sql(conn):
            conn.execute(sql)
        set_meta(conn, "triggers", TRIGGER_VERSION)
    conn.commit()
    return node(conn)


def disable(conn) -> None:
    if _table_exists(conn, "sync_meta"):
        _drop_triggers(conn)
        conn.execute("DELETE FROM sync_meta WHERE key='triggers'")
        conn.commit()


def _drop_triggers(conn) -> None:
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'sync\\_%' ESCAPE '\\'").fetchall():
        conn.execute(f"DROP TRIGGER IF EXISTS {_q(name)}")


def _log(tbl: str, k: str, col: str, op: str, data: str) -> str:
    return (f"INSERT INTO sync_log(tbl,k,col,op,data,at,origin) VALUES('{tbl}',{k},{col},'{op}',{data},{NOW},{NODE});")


def _trigger_sql(conn) -> list[str]:
    out = []
    # tracks: per column; a new track (made by a scan) carries everything plus its path on the server
    cols = [c for c in _cols(conn, "tracks") if c not in LOCAL_TRACK_COLS]
    ins = "json_object(" + ",".join(f"'{c}',NEW.{_q(c)}" for c in cols) + ",'_path',NEW.path)"
    out.append(f"CREATE TRIGGER sync_tracks_ins AFTER INSERT ON tracks WHEN {ON} BEGIN "
               + _log("tracks", "json_array(NEW.id)", "NULL", "ins", ins) + " END")
    out.append(f"CREATE TRIGGER sync_tracks_del BEFORE DELETE ON tracks WHEN {ON} BEGIN "
               + _log("tracks", "json_array(OLD.id)", "NULL", "del", "NULL") + " END")
    for c in cols:
        out.append(f"CREATE TRIGGER sync_tracks_{c} AFTER UPDATE OF {_q(c)} ON tracks "
                   f"WHEN {ON} AND OLD.{_q(c)} IS NOT NEW.{_q(c)} BEGIN "
                   + _log("tracks", "json_array(NEW.id)", f"'{c}'", "up", f"json_object('v',NEW.{_q(c)})") + " END")
    for tbl, spec in ROWS.items():
        if not _table_exists(conn, tbl):
            continue
        key, mood, blob = spec["key"], spec.get("mood"), spec.get("blob", set())
        vals = [c for c in _cols(conn, tbl) if c not in key and c not in spec.get("local", set())]

        def kexpr(row: str) -> str:
            parts = [f"(SELECT name FROM moods WHERE id={row}.{_q(c)})" if c == mood else f"{row}.{_q(c)}" for c in key]
            return "json_array(" + ",".join(parts) + ")"

        data = ("json_object(" + ",".join(f"'{c}',{'hex(NEW.' + _q(c) + ')' if c in blob else 'NEW.' + _q(c)}"
                                          for c in vals) + ")") if vals else "'{}'"
        out.append(f"CREATE TRIGGER sync_{tbl}_ins AFTER INSERT ON {tbl} WHEN {ON} BEGIN "
                   + _log(tbl, kexpr("NEW"), "NULL", "up", data) + " END")
        out.append(f"CREATE TRIGGER sync_{tbl}_del BEFORE DELETE ON {tbl} WHEN {ON} BEGIN "
                   + _log(tbl, kexpr("OLD"), "NULL", "del", "NULL") + " END")
        same = " AND ".join(f"OLD.{_q(c)} IS NEW.{_q(c)}" for c in key)
        if tbl == "moods":                         # a renamed mood keeps its labels everywhere
            out.append(f"CREATE TRIGGER sync_moods_ren AFTER UPDATE OF name ON moods "
                       f"WHEN {ON} AND OLD.name IS NOT NEW.name BEGIN "
                       + _log("moods", "json_array(OLD.name)", "NULL", "ren", "json_object('new',NEW.name)") + " END")
        else:
            out.append(f"CREATE TRIGGER sync_{tbl}_rekey AFTER UPDATE ON {tbl} WHEN {ON} AND NOT ({same}) BEGIN "
                       + _log(tbl, kexpr("OLD"), "NULL", "del", "NULL")
                       + _log(tbl, kexpr("NEW"), "NULL", "up", data) + " END")
        out.append(f"CREATE TRIGGER sync_{tbl}_upd AFTER UPDATE ON {tbl} WHEN {ON} AND {same} BEGIN "
                   + _log(tbl, kexpr("NEW"), "NULL", "up", data) + " END")
    # a playlist's track list travels whole
    lst = ("json_object('ids',(SELECT json_group_array(track_id) FROM "
           "(SELECT track_id FROM playlist_tracks WHERE playlist_id={r}.playlist_id ORDER BY pos)))")
    for ev, r in (("INSERT", "NEW"), ("UPDATE", "NEW"), ("DELETE", "OLD")):
        out.append(f"CREATE TRIGGER sync_playlist_tracks_{ev.lower()} AFTER {ev} ON playlist_tracks WHEN {ON} BEGIN "
                   + _log("playlist_tracks", f"json_array((SELECT name FROM playlists WHERE id={r}.playlist_id))",
                          "NULL", "up", lst.format(r=r)) + " END")
    return out


# ---- reading the journal ------------------------------------------------------------------------------------------
def entries_since(conn, since: int, limit: int = 2000, origin: str | None = None,
                  exclude_origin: str | None = None) -> tuple[list[dict], int]:
    """Journal entries after ``since`` (optionally only one origin / without one).  Returns (entries, last seq looked
    at) - the caller continues from the last seq even when every entry in the batch was filtered out."""
    rows = conn.execute("SELECT seq,tbl,k,col,op,data,at,origin FROM sync_log WHERE seq>? ORDER BY seq LIMIT ?",
                        (int(since), int(limit))).fetchall()
    last = rows[-1][0] if rows else int(since)
    out = []
    for seq, tbl, k, col, op, data, at, org in rows:
        if (origin and org != origin) or (exclude_origin and org == exclude_origin):
            continue
        out.append({"seq": seq, "tbl": tbl, "k": json.loads(k), "col": col, "op": op,
                    "data": json.loads(data) if data else None, "at": at, "origin": org})
    return out, last


def head(conn) -> int:
    return conn.execute("SELECT COALESCE(MAX(seq),0) FROM sync_log").fetchone()[0]


# ---- applying entries from elsewhere ------------------------------------------------------------------------------
def apply(db, entries: Iterable[dict], *, accept_new_tracks: bool = True,
          map_path: Callable[[str], str] | None = None) -> dict:
    """Apply entries made elsewhere (last writer wins) and journal them with their origin.

    ``accept_new_tracks``: a desktop takes the server's new tracks; the server ignores tracks made by a peer (their
    paths are the peer's own).  ``map_path(server_path) -> local_path`` for new tracks on a desktop (its copy of the
    music folder, when it has one; the file may not be there - it then plays from the server).
    Returns {"applied", "skipped", "new" (tracks), "tracks": [touched track ids]}."""
    conn = db.conn
    _COLS.clear()
    applied = skipped = new = 0
    touched: set[int] = set()
    feat: set[int] = set()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("UPDATE sync_flag SET applying=1")
        for e in entries:
            k = json.dumps(e["k"], ensure_ascii=False, separators=(",", ":"))
            kdb = conn.execute("SELECT json(?)", (k,)).fetchone()[0]     # SQLite's own spelling of the key
            col = e.get("col")
            newer = conn.execute("SELECT 1 FROM sync_log WHERE tbl=? AND k=? AND col IS ? AND at>? LIMIT 1",
                                 (e["tbl"], kdb, col, float(e["at"]))).fetchone()
            if newer or not _apply_one(conn, e, accept_new_tracks, map_path, touched, feat):
                skipped += 1
                continue
            new += e["tbl"] == "tracks" and e["op"] == "ins"
            conn.execute("INSERT INTO sync_log(tbl,k,col,op,data,at,origin) VALUES(?,?,?,?,?,?,?)",
                         (e["tbl"], kdb, col, e["op"],
                          json.dumps(e["data"], ensure_ascii=False) if e.get("data") is not None else None,
                          float(e["at"]), e["origin"]))
            applied += 1
        conn.execute("UPDATE sync_flag SET applying=0")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    for tid in feat:
        t = conn.execute("SELECT title, artist FROM tracks WHERE id=?", (tid,)).fetchone()
        if t:
            db._set_feat(tid, t[0], t[1])
    for tid in touched:
        db.refresh_fts(tid)
    if touched or feat:
        conn.commit()
    return {"applied": applied, "skipped": skipped, "new": new, "tracks": sorted(touched)}


def _mood_id(conn, name) -> int | None:
    if name is None:
        return None
    r = conn.execute("SELECT id FROM moods WHERE name=?", (name,)).fetchone()
    if r:
        return r[0]
    return conn.execute("INSERT INTO moods(name) VALUES(?)", (name,)).lastrowid


def _apply_one(conn, e: dict, accept_new_tracks: bool, map_path, touched: set, feat: set) -> bool:
    tbl, op, k, data = e["tbl"], e["op"], e["k"], e.get("data") or {}
    if tbl == "tracks":
        tid = int(k[0])
        if op == "ins":
            if not accept_new_tracks or conn.execute("SELECT 1 FROM tracks WHERE id=?", (tid,)).fetchone():
                return False
            path = map_path(data.get("_path", "")) if map_path else data.get("_path", "")
            if conn.execute("SELECT 1 FROM tracks WHERE path=?", (path,)).fetchone():
                path = f"{path}#{tid}"                 # a stale local row holds the path: keep both
            cols = [c for c in data if c != "_path" and c in _ccols(conn, "tracks")]
            conn.execute(f"INSERT INTO tracks(id,path,{','.join(_q(c) for c in cols)}) "
                         f"VALUES(?,?,{','.join('?' * len(cols))})", [tid, path] + [data[c] for c in cols])
            touched.add(tid)
            feat.add(tid)
            return True
        if op == "del":
            conn.execute("DELETE FROM tracks WHERE id=?", (tid,))
            return True
        col = e["col"]
        if col not in _ccols(conn, "tracks") or col in LOCAL_TRACK_COLS:
            return False
        cur = conn.execute(f"UPDATE tracks SET {_q(col)}=? WHERE id=?", (data.get("v"), tid))
        if cur.rowcount:
            touched.add(tid)
            if col in ("title", "artist"):
                feat.add(tid)
        return bool(cur.rowcount)
    if tbl == "playlist_tracks":
        if not k or k[0] is None:
            return False
        r = conn.execute("SELECT id FROM playlists WHERE name=?", (k[0],)).fetchone()
        pid = r[0] if r else conn.execute("INSERT INTO playlists(name) VALUES(?)", (k[0],)).lastrowid
        conn.execute("DELETE FROM playlist_tracks WHERE playlist_id=?", (pid,))
        conn.executemany("INSERT INTO playlist_tracks(playlist_id,pos,track_id) VALUES(?,?,?)",
                         [(pid, i, t) for i, t in enumerate(data.get("ids") or [])])
        return True
    spec = ROWS.get(tbl)
    if spec is None or not _table_exists(conn, tbl) or any(v is None for v in k):
        return False
    key, mood, blob = spec["key"], spec.get("mood"), spec.get("blob", set())
    kv = list(k)
    if mood:
        i = key.index(mood)
        kv[i] = _mood_id(conn, kv[i]) if op != "del" else (
            (conn.execute("SELECT id FROM moods WHERE name=?", (kv[i],)).fetchone() or [None])[0])
        if kv[i] is None:
            return op == "del"                       # nothing to delete here: still journaled as seen
    where = " AND ".join(f"{_q(c)}=?" for c in key)
    if "track_id" in key:
        touched.add(int(kv[key.index("track_id")]))
    if tbl == "moods" and op == "ren":
        new = data.get("new")
        if not new or conn.execute("SELECT 1 FROM moods WHERE name=?", (new,)).fetchone():
            return False
        conn.execute("UPDATE moods SET name=? WHERE name=?", (new, kv[0]))
        return True
    if op == "del":
        conn.execute(f"DELETE FROM {tbl} WHERE {where}", kv)
        return True
    cols = [c for c in data if c in _ccols(conn, tbl)]
    vals = [bytes.fromhex(data[c]) if c in blob and data[c] is not None else data[c] for c in cols]
    if conn.execute(f"SELECT 1 FROM {tbl} WHERE {where}", kv).fetchone():
        if cols:
            conn.execute(f"UPDATE {tbl} SET {','.join(_q(c) + '=?' for c in cols)} WHERE {where}", vals + kv)
    else:
        allc = key + cols
        conn.execute(f"INSERT INTO {tbl}({','.join(_q(c) for c in allc)}) VALUES({','.join('?' * len(allc))})",
                     kv + vals)
    return True


def compact(conn, keep_days: float = 30) -> int:
    """Drop journal entries that a newer entry for the same thing has replaced (older than ``keep_days``)."""
    cut = time.time() - keep_days * 86400
    n = conn.execute("""DELETE FROM sync_log WHERE at < ? AND EXISTS (SELECT 1 FROM sync_log n
                        WHERE n.tbl=sync_log.tbl AND n.k=sync_log.k AND n.col IS sync_log.col AND n.seq>sync_log.seq)""",
                     (cut,)).rowcount
    conn.commit()
    return n
