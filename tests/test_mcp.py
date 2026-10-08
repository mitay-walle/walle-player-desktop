"""The MCP server module must import, register every tool and answer real calls in-process (a syntax error in a
tool description once broke the whole exe while the GUI tests, which start without MCP, stayed green)."""
import asyncio
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

EXPECTED = {"status", "navigate", "screenshot", "scan", "search_tracks", "get_tracks", "list_values", "similar",
            "player_state", "play", "transport", "enqueue", "create_playlist", "set_genres", "set_moods", "batch_tag",
            "suggest_moods", "resolve_suggestions", "write_genres_to_files", "restore_genres", "listening_stats",
            "analyze", "train_model", "predict_moods", "analyze_clap", "zero_shot_moods", "clap_agreement",
            "fetch_web_tags", "web_mood_suggestions", "web_agreement", "web_tags", "next_step", "tracks_to_label",
            "start_wave", "wave_feedback", "start_label_feed", "label_feed_state", "set_ui", "job_status", "cancel_job"}


@pytest.fixture
def server(qtbot, tmp_path, library):
    from musicplayer.ui.app import create_app
    from musicplayer.ui.mcp_server import build_server
    _, ctx, win = create_app(home=tmp_path / "h", mcp=False)
    qtbot.addWidget(win)
    win.start_scan(str(library))
    qtbot.waitUntil(lambda: not ctx.jobs.busy, timeout=60000)
    yield build_server(win), ctx, win
    ctx.jobs.cancel()
    qtbot.waitUntil(lambda: not ctx.jobs.busy, timeout=30000)


def test_every_tool_is_registered(server):
    srv, _, _ = server
    names = {t.name for t in asyncio.run(srv.list_tools())}
    assert EXPECTED <= names, sorted(EXPECTED - names)


def test_create_app_survives_a_broken_mcp_module(qtbot, tmp_path, monkeypatch):
    import sys
    from musicplayer.ui.app import create_app
    monkeypatch.setitem(sys.modules, "musicplayer.ui.mcp_server", None)     # import raises ImportError
    _, ctx, win = create_app(home=tmp_path / "h2", mcp=True)
    qtbot.addWidget(win)
    assert win.mcp is None and "MCP" in win.statusBar().currentMessage()


def test_tools_answer_over_the_real_protocol(server, qtbot):
    """Call tools through an MCP client connected to the in-memory server; the GUI thread is pumped by qtbot."""
    srv, ctx, win = server
    import threading
    from mcp import Client

    out: dict = {}

    def run():
        async def go():
            async with Client(srv) as c:
                d = lambda r: r.structured_content or json.loads(r.content[0].text)  # noqa: E731
                out["status"] = d(await c.call_tool("status", {}))
                out["search"] = d(await c.call_tool("search_tracks", {"text": "кино", "limit": 5}))
                out["moods"] = d(await c.call_tool("set_moods", {"track_ids": [t["id"] for t in out["search"]["rows"]],
                                                                 "moods": ["Грустное"]}))
                out["step"] = d(await c.call_tool("next_step", {}))
        try:
            asyncio.run(go())
        except BaseException as e:   # reported by the assertions below
            out["error"] = repr(e)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    qtbot.waitUntil(lambda: not t.is_alive(), timeout=60000)
    assert "error" not in out, out.get("error")
    assert out["status"]["tracks"] == 3 and out["search"]["total"] == 2 and out["moods"]["updated"] == 2
    assert out["step"]["step"] == "label"
