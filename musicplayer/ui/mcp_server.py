"""In-process MCP server: Claude drives the running app (streamable HTTP, 127.0.0.1 only).

Every tool runs its GUI/db part on the Qt GUI thread through :class:`GuiBridge` and uses the same
page methods as the buttons, so the window visibly reacts (page switches, progress bar, chart, tables).
Long jobs (scan, analyze, train, predict) return when finished (``wait=True``) by polling from the server
thread, never by blocking the GUI.  Tag changes go to the database first; writing genres into files is a
separate tool with a dry run by default and a restore tool.
"""


import asyncio
import concurrent.futures
import logging
import threading
import time
from typing import Any, Callable

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Qt, Signal, Slot

from musicplayer.core import autotag, clap, mood, tagwrite, webtags
from musicplayer.core.similar import similar_tracks

log = logging.getLogger(__name__)
DEFAULT_PORT = 8766
CALL_TIMEOUT = 120.0


class GuiBridge(QObject):
    """Runs callables on the GUI thread and hands the result back to the caller thread."""

    _request = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._request.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, item: tuple[Callable[[], Any], concurrent.futures.Future]) -> None:
        fn, future = item
        if future.set_running_or_notify_cancel():
            try:
                future.set_result(fn())
            except BaseException as exc:  # noqa: BLE001 - delivered to the MCP caller
                future.set_exception(exc)

    def call(self, fn: Callable[[], Any], timeout: float = CALL_TIMEOUT) -> Any:
        future: concurrent.futures.Future = concurrent.futures.Future()
        self._request.emit((fn, future))
        return future.result(timeout=timeout)


def _png(widget) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    widget.grab().save(buf, "PNG")
    return bytes(data.data())


def brief(t: dict | None) -> dict | None:
    """Compact track row for tool results."""
    if not t:
        return None
    return {"id": t["id"], "title": t["title"], "artist": t["artist"], "album": t["album"], "year": t["year"],
            "feat": t.get("feat", []), "genres": t["genres"], "moods": t["moods"],
            "mood_suggestions": [f"{s['mood']} {int((s['confidence'] or 0) * 100)}% ({s['source']})"
                                 for s in t["mood_suggestions"]],
            "plays": t["play_count"], "plays_30d": t["plays_by_period"]["month"],
            "plays_365d": t["plays_by_period"]["year"], "last_played": t["last_played"],
            **({"similarity": t["similarity"], "basis": t["basis"]} if "similarity" in t else {})}


def build_server(window) -> Any:
    from mcp.server.mcpserver import Image, MCPServer

    ctx = window.ctx
    bridge = GuiBridge()
    bridge.moveToThread(window.thread())
    window._mcp_bridge = bridge
    player = ctx.player
    server = MCPServer(
        name="music-player",
        instructions=(
            "Controls the running Walle Player desktop app (library of ~3800 mp3). Everything you do is shown "
            "in its window. Start with status(). Tags (genres, moods) are edited in the database; writing "
            "genres into files is separate: write_genres_to_files(dry_run=True) first. Mood predictions from "
            "the model are suggestions until accepted."))

    def gui(fn: Callable[[], Any], timeout: float = CALL_TIMEOUT) -> Any:
        return bridge.call(fn, timeout)

    def announce(name: str) -> None:
        gui(lambda: window.statusBar().showMessage(f"MCP: {name}", 4000))

    async def run(fn: Callable[[], Any]) -> Any:
        return await asyncio.to_thread(fn)

    def wait_job(timeout: float) -> dict:
        deadline = time.time() + timeout
        time.sleep(0.3)
        while time.time() < deadline:
            if not gui(lambda: ctx.jobs.busy):
                break
            time.sleep(0.4)
        return gui(job_summary)

    def job_summary() -> dict:
        j = ctx.jobs.current or (ctx.jobs.history[-1] if ctx.jobs.history else None)
        if j is None:
            return {"job": None}
        res = j.result if isinstance(j.result, dict) else None
        if res and "history" in res:
            res = {k: v for k, v in res.items() if k != "history"} | {"epochs_logged": len(res["history"])}
        if res and "failed" in res:
            res = {**res, "failed": len(res["failed"])}
        if res and "errors" in res:
            res = {**res, "errors": len(res["errors"])}
        return {"job": {"kind": j.kind, "title": j.title, "state": j.state, "done": j.done, "total": j.total,
                        "message": j.message, "error": j.error, "result": res, "log_tail": j.logs[-5:]}}

    def pg(key: str):
        return window.pages[key]

    # ------------------------------------------------------------------ state
    @server.tool(description="App state: current page, library/mood/descriptor counts, player, running job.")
    async def status() -> dict:
        def get() -> dict:
            c = ctx.db.conn
            return {
                "page": window.current_page_key(),
                "tracks": c.execute("SELECT COUNT(*) FROM tracks WHERE missing=0").fetchone()[0],
                "next_step": mood.next_step(ctx.db, ctx.models)["text"],
                "with_moods": c.execute("SELECT COUNT(DISTINCT track_id) FROM track_moods WHERE source IN ('manual','seed','auto')").fetchone()[0],
                "with_auto_moods": c.execute("SELECT COUNT(DISTINCT track_id) FROM track_moods WHERE source='auto'").fetchone()[0],
                "suggestions": c.execute("SELECT COUNT(*) FROM track_moods WHERE source NOT IN ('manual','seed','auto')").fetchone()[0],
                "analyzed": c.execute("SELECT COUNT(*) FROM track_descriptors").fetchone()[0],
                "clap": {"available": clap.available(),
                         "embeddings": c.execute("SELECT COUNT(*) FROM track_embeddings WHERE model='clap'").fetchone()[0]},
                "model": (lambda m: {"tracks": m.info.get("tracks"), "moods": m.moods, "val": m.info.get("val")}
                          if m else None)(mood.load_model(ctx.models)),
                "player": {"state": player.state, "track": brief(player.current)},
                "busy": ctx.jobs.busy, "library_root": ctx.settings.library_root,
                "pending_genre_changes": len(ctx.db.genre_changes()),
            }
        return await run(lambda: gui(get))

    @server.tool(description="Switch page: library, albums, queue, playlists, colormusic, moods, stats.")
    async def navigate(page: str) -> str:
        announce(f"navigate {page}")
        await run(lambda: gui(lambda: window.set_page(page)))
        return page

    @server.tool(description="PNG screenshot of the app window (optionally after switching to `page`).")
    async def screenshot(page: str | None = None) -> Image:
        def shot() -> bytes:
            if page:
                window.set_page(page)
            return _png(window)
        return Image(data=await run(lambda: gui(shot)), format="png")

    @server.tool(description="Interface settings: zoom (0.7-2.0, whole UI), tile_size (64-400 px album tiles), "
                             "color_mode (bars|radial|aurora|pulse for the colour-music page), strip (show the "
                             "colour-music strip over the player bar). Returns the resulting values.")
    async def set_ui(zoom: float | None = None, tile_size: int | None = None, color_mode: str | None = None,
                     strip: bool | None = None) -> dict:
        def go():
            if zoom is not None:
                window.set_zoom(zoom)
            if tile_size is not None:
                pg("albums").set_tile(tile_size)
            if color_mode:
                pg("colormusic").set_mode(color_mode)
            if strip is not None:
                ctx.settings.color_strip = strip
                window.player_bar.strip.setVisible(strip)
            return {"zoom": window.zoom, "tile_size": pg("albums").tile, "color_mode": pg("colormusic").view.mode,
                    "strip": window.player_bar.strip.isVisible(),
                    "audio": "live" if pg("colormusic").view.an.live else "idle"}
        announce("set_ui")
        return await run(lambda: gui(go))

    # ------------------------------------------------------------------ library
    @server.tool(description="Scan the music folder (incremental by modification time; default = saved folder).")
    async def scan(root: str | None = None, full: bool = False, wait: bool = True, timeout: float = 3600) -> dict:
        announce("scan")
        await run(lambda: gui(lambda: (window.set_page("library"), window.start_scan(root, full))))
        return await run(lambda: wait_job(timeout)) if wait else {"started": True}

    @server.tool(description="Search/filter tracks. Text is searched in title, artist, album, genres, moods, year. "
                             "order: artist,title,album,year,play_count,last_played,added,duration,random,"
                             "rare_random (random weighted to rarely played),plays_period (needs plays_days). "
                             "plays_days restricts min/max_plays to the last N days (30=month, 365=year). "
                             "mood_status: confirmed|suggested|any. no_mood=true: tracks without a confirmed mood. "
                             "artist also matches featured artists parsed from titles ('feat.', 'ft.', 'при участии'); "
                             "feat=NAME finds only tracks where NAME is featured. "
                             "show=true displays the result in the Library page.")
    async def search_tracks(text: str = "", genre: str | None = None, mood_: str | None = None,
                            mood_status: str = "confirmed", no_mood: bool = False, artist: str | None = None,
                            feat: str | None = None, flag: str | None = None,
                            exclude_flags: list[str] | None = None, album: str | None = None, year_from: int | None = None, year_to: int | None = None,
                            min_plays: int | None = None, max_plays: int | None = None,
                            plays_days: int | None = None, not_played_days: int | None = None,
                            order: str = "artist", desc: bool = False, limit: int = 50, offset: int = 0,
                            show: bool = False) -> dict:
        kw = dict(text=text, genre=genre, mood=mood_, mood_status=mood_status, no_mood=no_mood, artist=artist,
                  feat=feat, flag=flag, exclude_flags=exclude_flags, album=album, year_from=year_from, year_to=year_to, min_plays=min_plays,
                  max_plays=max_plays, plays_days=plays_days, not_played_days=not_played_days, order=order, desc=desc)

        def get() -> dict:
            total = ctx.db.count(**kw)
            rows = ctx.db.search(**kw, limit=limit, offset=offset)
            if show:
                window.set_page("library")
                pg("library").set_rows(ctx.db.search(**kw, limit=5000), "Результат MCP")
            return {"total": total, "rows": [brief(r) for r in rows]}
        announce("search_tracks")
        return await run(lambda: gui(get))

    @server.tool(description="Details of tracks by id (tags, moods, suggestions, play counts by period).")
    async def get_tracks(track_ids: list[int]) -> list[dict]:
        return await run(lambda: gui(lambda: [brief(ctx.db.get_track(i)) for i in track_ids]))

    @server.tool(description="Group values with counts: kind = genre | artist | feat_artist | album | year | mood.")
    async def list_values(kind: str) -> list[dict]:
        def get():
            return ctx.db.list_moods() if kind == "mood" else ctx.db.list_values(kind)
        return await run(lambda: gui(get))

    @server.tool(description="Tracks similar to a track (audio descriptors/embeddings when analysed, else tags).")
    async def similar(track_id: int, limit: int = 20, same_artist: bool = True, show: bool = False) -> list[dict]:
        def get():
            res = similar_tracks(ctx.db, track_id, limit, same_artist)
            if show:
                window.set_page("library")
                pg("library").set_rows(res, "Похожие (MCP)")
            return [brief(r) for r in res]
        return await run(lambda: gui(get))

    # ------------------------------------------------------------------ player
    @server.tool(description="Player state: current track, position, volume, shuffle/repeat, queue (with ids).")
    async def player_state() -> dict:
        def get():
            s = player.snapshot()
            s["track"] = brief(s["track"])
            return s
        return await run(lambda: gui(get))

    @server.tool(description="Play tracks now. Give track_ids, or playlist_id, or filters like search_tracks "
                             "(text/genre/mood_/artist/album/order/plays...). shuffle: off|random|rare "
                             "('rare' = favour rarely played). start = index to start at.")
    async def play(track_ids: list[int] | None = None, playlist_id: int | None = None, text: str = "",
                   genre: str | None = None, mood_: str | None = None, artist: str | None = None,
                   album: str | None = None, max_plays: int | None = None, plays_days: int | None = None,
                   not_played_days: int | None = None, order: str = "artist", limit: int = 200,
                   shuffle: str | None = None, start: int = 0) -> dict:
        def go():
            if track_ids:
                rows = [ctx.db.get_track(i) for i in track_ids]
            elif playlist_id:
                rows = ctx.db.playlist_tracks(playlist_id)
            else:
                rows = ctx.db.search(text=text, genre=genre, mood=mood_, artist=artist, album=album,
                                     max_plays=max_plays, plays_days=plays_days, not_played_days=not_played_days,
                                     order=order, limit=limit)
            rows = [r for r in rows if r]
            if not rows:
                return {"played": 0}
            if shuffle:
                player.queue.set_shuffle(shuffle)
            player.play_tracks(rows, start)
            return {"played": len(rows), "now": brief(player.current)}
        announce("play")
        return await run(lambda: gui(go))

    @server.tool(description="Transport: action = pause | resume | toggle | next | prev | stop.")
    async def transport(action: str) -> dict:
        def go():
            {"pause": player.pause, "resume": player.play, "toggle": player.toggle, "next": player.next,
             "prev": player.prev, "stop": player.stop}[action]()
            return {"state": player.state, "track": brief(player.current)}
        announce(action)
        return await run(lambda: gui(go))

    @server.tool(description="Seek to a position in seconds.")
    async def seek(seconds: float) -> float:
        await run(lambda: gui(lambda: player.seek(seconds)))
        return seconds

    @server.tool(description="Volume 0..1.")
    async def set_volume(volume: float) -> float:
        await run(lambda: gui(lambda: player.set_volume(volume)))
        return volume

    @server.tool(description="Shuffle (off|random|rare) and/or repeat (off|all|one).")
    async def set_modes(shuffle: str | None = None, repeat: str | None = None) -> dict:
        def go():
            if shuffle:
                player.set_shuffle(shuffle)
            if repeat:
                player.set_repeat(repeat)
            return {"shuffle": player.queue.shuffle, "repeat": player.queue.repeat}
        return await run(lambda: gui(go))

    @server.tool(description="Add tracks to the queue (next=true: right after the current one). Same filters "
                             "as play() can select tracks instead of track_ids.")
    async def enqueue(track_ids: list[int] | None = None, next_: bool = False, text: str = "",
                      genre: str | None = None, mood_: str | None = None, artist: str | None = None,
                      order: str = "artist", limit: int = 50) -> dict:
        def go():
            rows = ([ctx.db.get_track(i) for i in track_ids] if track_ids else
                    ctx.db.search(text=text, genre=genre, mood=mood_, artist=artist, order=order, limit=limit))
            rows = [r for r in rows if r]
            player.enqueue(rows, next_)
            return {"added": len(rows), "queue_length": len(player.queue.items)}
        announce("enqueue")
        return await run(lambda: gui(go))

    @server.tool(description="Jump to queue item (queue_index from player_state) or clear the queue.")
    async def queue_control(action: str, queue_index: int | None = None) -> dict:
        def go():
            if action == "jump" and queue_index is not None:
                player.jump(queue_index)
            elif action == "clear":
                pg("queue").clear()
            elif action == "remove" and queue_index is not None:
                player.queue.remove(queue_index)
                player.queue_changed.emit()
            return {"queue_length": len(player.queue.items), "now": brief(player.current)}
        return await run(lambda: gui(go))

    # ------------------------------------------------------------------ playlists
    @server.tool(description="List playlists (smart ones have `rules`).")
    async def list_playlists() -> list[dict]:
        return await run(lambda: gui(ctx.db.list_playlists))

    @server.tool(description="Create a playlist. Plain: pass track_ids. Smart: pass `rules` = search_tracks filters, "
                             "e.g. {\"genre\":\"Rock\",\"mood\":\"Грустное\",\"not_played_days\":60,"
                             "\"order\":\"rare_random\",\"limit\":50} (rules are evaluated live).")
    async def create_playlist(name: str, track_ids: list[int] | None = None, rules: dict | None = None) -> dict:
        def go():
            pid = ctx.db.create_playlist(name, rules)
            if track_ids:
                ctx.db.playlist_add(pid, track_ids)
            ctx.events.library_changed.emit()
            return {"id": pid, "count": len(ctx.db.playlist_tracks(pid))}
        announce("create_playlist")
        return await run(lambda: gui(go))

    @server.tool(description="Tracks of a playlist; show=true opens it on the Playlists page.")
    async def playlist_tracks(playlist_id: int, show: bool = False) -> list[dict]:
        def go():
            if show:
                window.set_page("playlists")
                pg("playlists").select_playlist(playlist_id)
            return [brief(t) for t in ctx.db.playlist_tracks(playlist_id)]
        return await run(lambda: gui(go))

    @server.tool(description="Edit a playlist: rename, replace smart rules, add_ids, remove_ids.")
    async def edit_playlist(playlist_id: int, name: str | None = None, rules: dict | None = None,
                            add_ids: list[int] | None = None, remove_ids: list[int] | None = None) -> dict:
        def go():
            ctx.db.update_playlist(playlist_id, name, rules)
            if add_ids:
                ctx.db.playlist_add(playlist_id, add_ids)
            if remove_ids:
                ctx.db.playlist_remove(playlist_id, remove_ids)
            ctx.events.library_changed.emit()
            return {"count": len(ctx.db.playlist_tracks(playlist_id))}
        return await run(lambda: gui(go))

    @server.tool(description="Delete a playlist (tracks stay in the library).")
    async def delete_playlist(playlist_id: int) -> bool:
        def go():
            ctx.db.delete_playlist(playlist_id)
            ctx.events.library_changed.emit()
            return True
        return await run(lambda: gui(go))

    # ------------------------------------------------------------------ genres & moods
    @server.tool(description="Change genres in the DATABASE (not in files). mode: set | add | remove. "
                             "Several tracks at once.")
    async def set_genres(track_ids: list[int], genres: list[str], mode: str = "set") -> dict:
        def go():
            ctx.db.set_genres(track_ids, genres, mode)
            ctx.events.tracks_changed.emit(list(track_ids))
            return {"updated": len(track_ids)}
        announce("set_genres")
        return await run(lambda: gui(go))

    @server.tool(description="Set CONFIRMED moods for tracks. mode: set | add | remove. New mood names are created.")
    async def set_moods(track_ids: list[int], moods: list[str], mode: str = "set") -> dict:
        def go():
            ctx.db.set_moods(track_ids, moods, mode)
            ctx.events.library_changed.emit()
            ctx.events.tracks_changed.emit(list(track_ids))
            return {"updated": len(track_ids)}
        announce("set_moods")
        return await run(lambda: gui(go))

    @server.tool(description="Batch tag edit: items = [{track_id, genres?:[..], moods?:[..], genre_mode?, mood_mode?}].")
    async def batch_tag(items: list[dict]) -> dict:
        def go():
            for it in items:
                if it.get("genres") is not None:
                    ctx.db.set_genres([it["track_id"]], it["genres"], it.get("genre_mode", "set"))
                if it.get("moods") is not None:
                    ctx.db.set_moods([it["track_id"]], it["moods"], it.get("mood_mode", "set"))
            ctx.events.library_changed.emit()
            ctx.events.tracks_changed.emit([it["track_id"] for it in items])
            return {"updated": len(items)}
        announce("batch_tag")
        return await run(lambda: gui(go))

    @server.tool(description="Recording-type flags of tracks: live | acoustic | demo | remix | cover. mode: set | add | "
                             "remove. Detected automatically from titles/albums; a manual edit freezes the track's flags. "
                             "search_tracks can filter with flag=... or exclude_flags=[...].")
    async def set_flags(track_ids: list[int], flags: list[str], mode: str = "set") -> dict:
        def go():
            ctx.db.set_flags(track_ids, flags, mode, "manual")
            ctx.events.tracks_changed.emit(list(track_ids))
            return {"updated": len(track_ids)}
        return await run(lambda: gui(go))

    @server.tool(description="Add a mood to the mood list (optional text prompt describing it).")
    async def add_mood(name: str, prompt: str | None = None) -> dict:
        def go():
            ctx.db.add_mood(name, prompt)
            ctx.events.library_changed.emit()
            return {"moods": [m["name"] for m in ctx.db.list_moods()]}
        return await run(lambda: gui(go))

    @server.tool(description="Correct track tags in the catalogue (files untouched; survives rescans): fields may hold "
                             "title, artist, albumartist, album, year. An empty string removes a correction.")
    async def edit_tracks(track_ids: list[int], fields: dict) -> dict:
        def go():
            n = ctx.db.edit_track(track_ids, fields)
            ctx.events.tracks_changed.emit(list(track_ids))
            ctx.events.library_changed.emit()
            return {"edited": n, "tracks": [ctx.db.get_track(i) and {k: ctx.db.get_track(i)[k] for k in
                                                                     ("id", "title", "artist", "album", "year")}
                                            for i in track_ids[:20]]}
        return await run(lambda: gui(go))

    @server.tool(description="Artists overview: per artist - tracks, albums, years, plays, top genres and moods, "
                             "voice, share of tracks with the user's mood labels.")
    async def artists_overview(limit: int = 100, query: str | None = None) -> dict:
        def go():
            rows = ctx.db.artists_overview()
            if query:
                rows = [r for r in rows if query.casefold() in " ".join([r["artist"], *r["genres"], *r["moods"]]).casefold()]
            return {"total": len(rows), "artists": rows[:limit]}
        return await run(lambda: gui(go))

    @server.tool(description="Favourites. kind: track (key = track id) | album (key = 'artist|album') | artist "
                             "(key = name). on=true adds, false removes; with no key: list that kind.")
    async def favorites(kind: str = "track", key: str | None = None, on: bool = True) -> dict:
        def go():
            from musicplayer.ui.pages.favorites import SEP
            if key is not None:
                k = key.replace("|", SEP, 1) if kind == "album" else str(key)
                ctx.db.set_favorite(kind, k, on)
                ctx.events.favorites_changed.emit()
            keys = ctx.db.favorites(kind)
            if kind == "track":
                return {"tracks": [t and {f: t[f] for f in ("id", "title", "artist", "album")}
                                   for t in (ctx.db.get_track(int(x)) for x in keys)]}
            return {kind + "s": [x.replace(SEP, " | ") for x in keys]}
        return await run(lambda: gui(go))

    @server.tool(description="Rename a mood (its labels keep the new name).")
    async def rename_mood(old: str, new: str) -> dict:
        def go():
            ctx.db.rename_mood(old, new)
            ctx.db.conn.commit()
            ctx.events.library_changed.emit()
            return {"moods": [m["name"] for m in ctx.db.list_moods()]}
        return await run(lambda: gui(go))

    @server.tool(description="Fold mood `old` into mood `into`: labels, suggestions and rejections move over, `old` "
                             "disappears (use when two moods mean the same).")
    async def merge_moods(old: str, into: str) -> dict:
        def go():
            ctx.db.merge_mood(old, into)
            ctx.db.conn.commit()
            ctx.events.library_changed.emit()
            return {"moods": [m["name"] for m in ctx.db.list_moods()]}
        return await run(lambda: gui(go))

    @server.tool(description="Delete a mood and ALL its labels (irreversible; the user should have asked for it).")
    async def delete_mood(name: str) -> dict:
        def go():
            ctx.db.delete_mood(name)
            ctx.db.conn.commit()
            ctx.events.library_changed.emit()
            return {"moods": [m["name"] for m in ctx.db.list_moods()]}
        return await run(lambda: gui(go))

    @server.tool(description="Claude's mood PROPOSALS: items = [{track_id, mood, confidence?}]. They are shown "
                             "as suggestions in the UI and never override confirmed labels.")
    async def suggest_moods(items: list[dict]) -> dict:
        def go():
            n = ctx.db.suggest_moods([(i["track_id"], i["mood"], i.get("confidence")) for i in items], "claude")
            ctx.events.library_changed.emit()
            ctx.events.tracks_changed.emit([i["track_id"] for i in items])
            return {"stored": n}
        announce("suggest_moods")
        return await run(lambda: gui(go))

    @server.tool(description="Accept (->confirmed) or reject suggestions of tracks. accept=true/false; optional "
                             "moods filter and min_confidence. track_ids empty = all tracks with suggestions.")
    async def resolve_suggestions(accept: bool, track_ids: list[int] | None = None, moods: list[str] | None = None,
                                  min_confidence: float | None = None) -> dict:
        def go():
            ids = track_ids or [r[0] for r in ctx.db.conn.execute(
                "SELECT DISTINCT track_id FROM track_moods WHERE source NOT IN ('manual','seed')")]
            n = ctx.db.resolve_suggestions(ids, accept, moods, min_confidence)
            ctx.events.library_changed.emit()
            ctx.events.tracks_changed.emit(ids)
            return {"resolved": n}
        return await run(lambda: gui(go))

    @server.tool(description="Genres that differ between the DB and the file tags (not yet written to files).")
    async def pending_genre_changes(limit: int = 100) -> dict:
        def go():
            ch = ctx.db.genre_changes()
            return {"total": len(ch), "rows": ch[:limit]}
        return await run(lambda: gui(go))

    @server.tool(description="Write DB genres into the audio files. dry_run=true (default) only reports. "
                             "Real write needs dry_run=false; previous tags are backed up for restore_genres.")
    async def write_genres_to_files(track_ids: list[int] | None = None, dry_run: bool = True) -> dict:
        announce("write_genres_to_files" + (" (dry run)" if dry_run else ""))
        return await run(lambda: gui(lambda: tagwrite.write_genres(ctx.db, track_ids, dry_run)))

    @server.tool(description="Undo write_genres_to_files: restore the genre tags that were replaced.")
    async def restore_genres(track_ids: list[int] | None = None) -> dict:
        return await run(lambda: gui(lambda: tagwrite.restore_genres(ctx.db, track_ids)))

    # ------------------------------------------------------------------ stats
    @server.tool(description="Listening stats. period_days limits to a window (7 week, 30 month, 365 year, "
                             "None = all time): most played tracks/artists/genres, tracks not played for "
                             "stale_days, plays per period.")
    async def listening_stats(period_days: int | None = None, top: int = 20, stale_days: int = 90) -> dict:
        def go():
            s = ctx.db.stats(top, period_days, stale_days)
            s["most_played"] = [{**brief(t), "plays_in_period": t["plays"]} for t in s["most_played"]]
            s["not_played_recently"] = [brief(t) for t in s["not_played_recently"]]
            return s
        announce("listening_stats")
        return await run(lambda: gui(lambda: (window.set_page("stats"), go())[1]))

    # ------------------------------------------------------------------ mood model
    @server.tool(description="Compute audio descriptors (Freesound-style) for track_ids, or scope: labeled | all "
                             "(~1.2 s per track per process). Waits for the job.")
    async def analyze(scope: str = "labeled", track_ids: list[int] | None = None, wait: bool = True,
                      timeout: float = 7200) -> dict:
        announce("analyze")
        await run(lambda: gui(lambda: (window.set_page("moods"), pg("moods").start_analyze(scope, track_ids))))
        return await run(lambda: wait_job(timeout)) if wait else {"started": True}

    @server.tool(description="Train the mood model on confirmed labels (needs analysed tracks; the Moods page "
                             "shows loss/F1 live). features: auto | descriptors | clap | both (CLAP embeddings need "
                             "analyze_clap first). web: auto | always | never - internet tags (fetch_web_tags) as extra features; 'auto' "
                             "keeps them only if the honest artist-grouped check shows a clear gain (the result reports "
                             "web_gain_auc either way).")
    async def train_model(epochs: int = 120, features: str = "auto", web: str = "auto", wait: bool = True,
                          timeout: float = 1800) -> dict:
        announce("train_model")
        choice = {"auto": "auto", "always": True, "never": False}.get(web, "auto")
        await run(lambda: gui(lambda: (window.set_page("moods"), pg("moods").start_train(epochs, features, choice))))
        return await run(lambda: wait_job(timeout)) if wait else {"started": True}

    @server.tool(description="Predict moods with the trained model -> suggestions (analysed tracks only).")
    async def predict_moods(threshold: float = 0.5, only_unlabelled: bool = True, wait: bool = True,
                            timeout: float = 1800) -> dict:
        announce("predict_moods")
        await run(lambda: gui(lambda: (window.set_page("moods"),
                                       pg("moods").start_predict(only_unlabelled, threshold))))
        return await run(lambda: wait_job(timeout)) if wait else {"started": True}

    @server.tool(description="What to do next in the mood workflow (label -> analyse -> train -> predict -> review): "
                             "returns {step, action, text}.")
    async def next_step() -> dict:
        return await run(lambda: gui(lambda: mood.next_step(ctx.db, ctx.models)))

    @server.tool(description="Active learning: analysed tracks without a confirmed mood on which the model is least "
                             "sure (label these first). show=true lists them in the Library page.")
    async def tracks_to_label(limit: int = 20, show: bool = False) -> list[dict]:
        def go():
            rows = mood.uncertain_tracks(ctx.db, ctx.models, limit)
            if show and rows:
                ctx.events.show_rows.emit(rows, "Что разметить следующим")
                window.set_page("library")
            return [{**brief(r), "uncertainty": r.get("uncertainty")} for r in rows]
        return await run(lambda: gui(go))

    @server.tool(description="CLAP embeddings (ready-made audio-text model, run in a separate python process): "
                             "scope labeled | all, or track_ids. Needed for clap/both features, zero-shot and audio-based "
                             "similar tracks. ~10 s model load, then fast on GPU.")
    async def analyze_clap(scope: str = "labeled", track_ids: list[int] | None = None, wait: bool = True,
                           timeout: float = 7200) -> dict:
        announce("analyze_clap")
        await run(lambda: gui(lambda: (window.set_page("moods"), pg("moods").start_clap(scope, track_ids))))
        return await run(lambda: wait_job(timeout)) if wait else {"started": True}

    @server.tool(description="Mood suggestions straight from CLAP with NO training: matches the audio to the text "
                             "prompt of each mood (edit prompts via add_mood). Needs CLAP embeddings.")
    async def zero_shot_moods(threshold: float = 0.3, only_unlabelled: bool = True, wait: bool = True,
                              timeout: float = 1800) -> dict:
        announce("zero_shot_moods")
        await run(lambda: gui(lambda: (window.set_page("moods"), pg("moods").start_zero_shot(only_unlabelled, threshold))))
        return await run(lambda: wait_job(timeout)) if wait else {"started": True}

    @server.tool(description="How well CLAP zero-shot agrees with the moods YOU confirmed (top-1 accuracy and "
                             "recall per mood) - tells whether the ready-made model fits your taste.")
    async def clap_agreement(timeout: float = 600) -> dict:
        await run(lambda: gui(lambda: pg("moods").start_agreement()))
        return await run(lambda: wait_job(timeout))

    @server.tool(description="Open the labelling feed (one track plays at a time, the user presses mood keys) with a "
                             "source: uncertain (model unsure) | random | review (tracks with suggestions) | library "
                             "(current Library list). Returns how many tracks are queued.")
    async def start_label_feed(source: str = "uncertain", count: int = 50) -> dict:
        def go():
            window.set_page("labelfeed")
            n = pg("labelfeed").start(source, count)
            return {"queued": n, "now": brief(pg("labelfeed").current)}
        announce("start_label_feed")
        return await run(lambda: gui(go))

    @server.tool(description="State of the labelling feed: current track, chosen moods, counters.")
    async def label_feed_state() -> dict:
        def go():
            f = pg("labelfeed")
            return {"current": brief(f.current), "chosen": sorted(f.chosen), "labelled": f.done, "skipped": f.skipped,
                    "left": max(0, len(f.queue) - f.pos - 1) if f.current else 0}
        return await run(lambda: gui(go))

    @server.tool(description="Start 'My wave': endless listening by mood (several moods allowed, none = any labelled "
                             "mood). character: favorite | unfamiliar | mixed | weak (least certain guesses). A share of tracks are model GUESSES to "
                             "be rated with wave_feedback (like confirms the mood, dislike rejects it).")
    async def start_wave(moods: list[str] | None = None, character: str = "mixed") -> dict:
        def go():
            window.set_page("wave")
            n = pg("wave").start(moods or [], character)
            cur = player.current
            return {"queued": n, "now": brief(cur), "kind": cur.get("wave_kind") if cur else None}
        announce("start_wave")
        return await run(lambda: gui(go))

    @server.tool(description="Rate the track now playing in the wave: like=true confirms the guessed mood (new label), "
                             "like=false rejects it (negative example) and skips; like=false with moods=[...] stores those moods as the "
                             "track's own (manual) labels instead.")
    async def wave_feedback(like: bool, moods: list[str] | None = None) -> dict:
        def go():
            w = pg("wave")
            cur = player.current
            if not (w.active and cur):
                return {"error": "the wave is not playing"}
            was = cur.get("wave_kind")
            if not like and moods:                       # "not this one, these fit": the correction path
                w.open_fix()
                w.fix_selected = set(moods)
                w.save_fix()
            elif like:
                w.rate(True)
            else:
                w.reject_and_skip()
            return {"was": was, "message": w.toast.text()}
        return await run(lambda: gui(go))

    @server.tool(description="Fetch tags and moods from the INTERNET for tracks: MusicBrainz (recording + artist tags), "
                             "AcousticBrainz (ready-made happy/sad/aggressive/relaxed/party predictions) and, if "
                             "MUSICPLAYER_LASTFM_KEY is set, Last.fm crowd tags. scope labeled | all, or track_ids. "
                             "~1.1 s per track (MusicBrainz limit); cached, so each track is asked once.")
    async def fetch_web_tags(scope: str = "labeled", track_ids: list[int] | None = None, wait: bool = True,
                             timeout: float = 14400) -> dict:
        announce("fetch_web_tags")
        await run(lambda: gui(lambda: (window.set_page("moods"), pg("moods").start_web(scope, track_ids))))
        return await run(lambda: wait_job(timeout)) if wait else {"started": True}

    @server.tool(description="Turn the fetched AcousticBrainz moods into mood SUGGESTIONS (source 'web') for tracks "
                             "with probability >= min_conf. The user confirms or rejects them.")
    async def web_mood_suggestions(min_conf: float = 0.7) -> dict:
        return await run(lambda: gui(lambda: pg("moods").web_suggest(min_conf)))

    @server.tool(description="How often strong internet mood guesses match the moods the user confirmed "
                             "(precision overall and per mood).")
    async def web_agreement() -> dict:
        return await run(lambda: gui(lambda: webtags.agreement(ctx.db)))

    @server.tool(description="Internet tags and AcousticBrainz moods stored for a track.")
    async def web_tags(track_id: int) -> dict | None:
        return await run(lambda: gui(lambda: webtags.get_web(ctx.db, track_id)))

    @server.tool(description="Automatic mood for tracks the model has not seen: computes the features the trained model "
                             "needs (descriptors / CLAP / internet tags) and assigns moods with probability >= threshold "
                             "(source 'auto', shown with a mark; weaker guesses become suggestions). track_ids = only "
                             "these, default = every track without a label. Runs automatically after every scan when "
                             "auto-tagging is enabled.")
    async def auto_tag_new(track_ids: list[int] | None = None, wait: bool = True, timeout: float = 14400) -> dict:
        announce("auto_tag_new")
        await run(lambda: gui(lambda: window.start_autotag(track_ids)))
        return await run(lambda: wait_job(timeout)) if wait else {"started": True}

    @server.tool(description="Auto-tagging settings: enabled (tag new tracks after each scan) and mode (strict | "
                             "balanced | wide: how readily the model assigns moods by itself; each mood has its own "
                             "threshold learned from honest validation). Returns current values.")
    async def auto_tag_settings(enabled: bool | None = None, mode: str | None = None) -> dict:
        def go():
            if enabled is not None:
                ctx.settings.auto_tag = enabled
            if mode in ("strict", "balanced", "wide"):
                ctx.settings.auto_mode = mode
            ctx.save_settings()
            return {"enabled": ctx.settings.auto_tag, "mode": ctx.settings.auto_mode,
                    "pending": len(autotag.pending_ids(ctx.db)), "model": mood.load_model(ctx.models) is not None}
        return await run(lambda: gui(go))

    @server.tool(description="Apply a REVIEW batch (Claude checked the model's moods track by track). corrections = "
                             "{track_id: [moods]} - the reviewer's own labels (source 'seed', used for training); "
                             "accept_ids = tracks whose current moods are correct (auto/suggested moods become 'seed'); "
                             "all listed tracks are marked reviewed. The Library page updates live.")
    async def review_apply(corrections: dict[str, list[str]] | None = None, accept_ids: list[int] | None = None,
                           skip_ids: list[int] | None = None) -> dict:
        def go():
            db = ctx.db
            db.conn.execute("CREATE TABLE IF NOT EXISTS reviewed(track_id INTEGER PRIMARY KEY, at REAL)")
            import time
            changed = []
            for tid, moods in (corrections or {}).items():
                tid = int(tid)
                db.set_moods([tid], moods, "set")
                db.conn.execute("UPDATE track_moods SET source='seed' WHERE track_id=? AND source='manual'", (tid,))
                changed.append(tid)
            for tid in accept_ids or []:
                n = db.conn.execute("UPDATE track_moods SET source='seed' WHERE track_id=? AND source IN ('auto','model','web','clap')",
                                    (tid,)).rowcount
                db.refresh_fts(tid)
                changed.append(tid) if n else None
            done = {int(t) for t in (corrections or {})} | set(accept_ids or []) | set(skip_ids or [])
            db.conn.executemany("INSERT OR REPLACE INTO reviewed VALUES(?,?)", [(t, time.time()) for t in done])
            ctx.events.library_changed.emit()
            ctx.events.tracks_changed.emit(changed)
            window.statusBar().showMessage(f"Проверено мной: {len(done)} треков (исправлено {len(corrections or {})})", 6000)
            return {"reviewed": len(done), "corrected": len(corrections or {}),
                    "total_reviewed": db.conn.execute("SELECT COUNT(*) FROM reviewed").fetchone()[0]}
        return await run(lambda: gui(go))

    @server.tool(description="Library server for phones and other computers («Другие устройства»): action = status | on | "
                             "off | tunnel_on | tunnel_off (Cloudflare quick tunnel for internet access, needs cloudflared) | "
                             "add_user (name, can_label) | remove_user (user_id). Every listener has an own key, likes and "
                             "history. Returns per-listener pairing links (they contain the key) and connected devices.")
    async def remote_share(action: str = "status", name: str = "", can_label: bool = False, user_id: str = "") -> dict:
        sh = window.sharing

        def go():
            if action == "on":
                sh.start()
            elif action == "off":
                sh.stop()
            elif action == "tunnel_on":
                sh.start_tunnel()
            elif action == "tunnel_off":
                sh.stop_tunnel()
            elif action == "add_user" and name.strip():
                sh.add_user(name, can_label)
            elif action == "remove_user":
                sh.remove_user(user_id)
            return sh.status()
        res = await run(lambda: gui(go))
        if action == "tunnel_on":                       # cloudflared needs a few seconds to get the address
            for _ in range(60):
                if not sh.tunnel_busy:
                    break
                await asyncio.sleep(0.5)
            res = await run(lambda: gui(sh.status))
        return res

    @server.tool(description="State of the current/last background job.")
    async def job_status() -> dict:
        return await run(lambda: gui(job_summary))

    @server.tool(description="Cancel the running background job.")
    async def cancel_job() -> bool:
        await run(lambda: gui(ctx.jobs.cancel))
        return True

    return server


class McpServerThread:
    """Owns the uvicorn server thread; ``start()`` / ``stop()`` from the GUI thread."""

    def __init__(self, window, port: int = DEFAULT_PORT) -> None:
        self.window = window
        self.port = int(port)
        self._thread: threading.Thread | None = None
        self._server = None
        self.error: str | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        try:
            import uvicorn

            mcp = build_server(self.window)
            app = mcp.streamable_http_app(host="127.0.0.1")
            config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning",
                                    lifespan="on", log_config=None)
            self._server = uvicorn.Server(config)
        except Exception as exc:  # noqa: BLE001 - the app works without MCP
            self.error = str(exc)
            log.exception("MCP server could not be created")
            return False

        def serve() -> None:
            try:
                asyncio.run(self._server.serve())
            except Exception as exc:  # noqa: BLE001
                self.error = str(exc)
                log.exception("MCP server stopped with an error")

        self._thread = threading.Thread(target=serve, name="mcp-server", daemon=True)
        self._thread.start()
        log.info("MCP server on %s", self.url)
        return True

    def stop(self, timeout: float = 3.0) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None
