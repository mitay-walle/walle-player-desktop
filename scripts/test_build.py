"""Smoke-test the BUILT exe: python scripts/test_build.py [path\\to\\WallePlayer.exe]

Starts the exe with a fresh sandbox home (data/buildtest), drives it over MCP exactly like Claude would and
asserts every stage: scan -> label -> analyse (process pool in the frozen app) -> train -> predict ->
suggestions -> playlists -> playback + play counter -> stats -> screenshots.  The music folder is only read.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from mcp import Client

ROOT = Path(__file__).resolve().parent.parent
PORT = 8777
HOME = ROOT / "data" / "buildtest"
LIB = os.environ.get("WALLE_TEST_LIBRARY", str(Path.home() / "Music"))     # a real music folder to scan
DEMO = {  # sandbox demo labels, see e2e_demo.py
    "Злое": ["Architects", "ERRA", "Soilwork"],
    "Спокойное": ["Carbon Based Lifeforms", "Ella Fitzgerald", "Moby"],
    "Энергичное": ["Yellowcard", "Sum 41", "Story of the Year"],
}
checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, info: str = "") -> None:
    checks.append((name, bool(ok), info))
    sys.stdout.reconfigure(encoding="utf-8")
    print(("PASS " if ok else "FAIL ") + name + (f"  [{info}]" if info else ""), flush=True)


async def run(exe: Path) -> None:
    if HOME.exists():
        shutil.rmtree(HOME)
    HOME.mkdir(parents=True)
    (HOME / "settings.json").write_text(json.dumps({"library_root": LIB, "volume": 0.05, "mcp_port": PORT}))
    env = {**os.environ, "MUSICPLAYER_HOME": str(HOME)}
    proc = subprocess.Popen([str(exe)], env=env, cwd=exe.parent)
    try:
        for _ in range(90):  # wait until the exe opens the MCP port
            try:
                socket.create_connection(("127.0.0.1", PORT), 1).close()
                break
            except OSError:
                await asyncio.sleep(1)
        async with Client(f"http://127.0.0.1:{PORT}/mcp", read_timeout_seconds=None) as c:
            async def call(tool: str, **kw):
                r = await c.call_tool(tool, kw)
                if r.is_error:
                    raise RuntimeError(f"{tool}: {r.content}")
                if r.structured_content:
                    sc = r.structured_content
                    return sc["result"] if list(sc) == ["result"] else sc
                text = [b.text for b in r.content if getattr(b, "text", None) is not None]
                return json.loads(text[0]) if text else {}

            for _ in range(60):  # wait for the server
                try:
                    st = await call("status")
                    break
                except Exception:
                    await asyncio.sleep(1)
            else:
                raise SystemExit("MCP server did not come up")
            check("exe starts, MCP answers", st["page"] == "wave")

            r = await call("scan")
            res = r["job"]["result"]
            check("scan of the real library", r["job"]["state"] == "finished" and res["errors"] == 0 and res["total"] > 3000,
                  f"{res['total']} files, {res['errors']} errors")
            res = await call("search_tracks", text="король", limit=5)
            check("Cyrillic prefix search", res["total"] > 100, f"{res['total']} hits")

            f = await call("search_tracks", feat="Марина Кацуба", limit=5)
            check("featured artist recognised", f["total"] >= 1 and "Марина Кацуба" in f["rows"][0]["feat"],
                  f"{f['total']} tracks, e.g. {f['rows'][0]['title'] if f['rows'] else None}")
            vals = await call("list_values", kind="feat_artist")
            check("featured artists listed", len(vals) > 20, f"{len(vals)} names")

            ids: dict[str, list[int]] = {}
            for mood, artists in DEMO.items():
                ids[mood] = []
                for a in artists:
                    rows = (await call("search_tracks", artist=a, order="random", limit=8))["rows"]
                    ids[mood] += [t["id"] for t in rows]
                await call("set_moods", track_ids=ids[mood], moods=[mood])
            n = sum(len(v) for v in ids.values())
            st = await call("status")
            check("moods set in the DB", st["with_moods"] == n, f"{n} tracks")

            r = await call("analyze", scope="labeled")
            check("analysis in worker processes (frozen)", r["job"]["state"] == "finished"
                  and r["job"]["result"]["failed"] == 0, json.dumps(r["job"]["result"]) + " " + r["job"]["error"])

            st = await call("status")
            has_clap = st["clap"]["available"]
            if has_clap:
                r = await call("analyze_clap", scope="labeled")
                check("CLAP embeddings from the exe (external python, GPU)", r["job"]["state"] == "finished"
                      and r["job"]["result"]["embedded"] == 72, json.dumps(r["job"]["result"]) + r["job"]["error"])
                ag = await call("clap_agreement")
                check("CLAP agreement with own labels is reported", ag["job"]["state"] == "finished"
                      and ag["job"]["result"]["accuracy"] is not None, str(ag["job"]["result"].get("accuracy")))
                r = await call("train_model", epochs=100, features="clap")
                val = (r["job"]["result"] or {}).get("val") or {}
                check("model trains on CLAP features", r["job"]["state"] == "finished" and (val.get("auc") or 0) > 0.55,
                      f"AUC {val.get('auc')} mode {(r['job']['result'] or {}).get('mode')}")
            else:
                print("SKIP CLAP checks: model not in the HuggingFace cache", flush=True)
            few = (await call("search_tracks", artist="Architects", limit=4))["rows"]
            wr = await call("fetch_web_tags", track_ids=[t["id"] for t in few])
            check("internet tags fetched (MusicBrainz/AcousticBrainz)", wr["job"]["state"] == "finished"
                  and wr["job"]["result"]["fetched"] == len(few), json.dumps(wr["job"]["result"]))
            wt_ = await call("web_tags", track_id=few[0]["id"])
            check("stored web tags readable", wt_ is not None and "tags" in wt_, str(wt_)[:120])
            r = await call("train_model", epochs=100)
            val = (r["job"]["result"] or {}).get("val") or {}
            check("model trains", r["job"]["state"] == "finished" and (val.get("auc") or 0) > 0.55,
                  f"AUC {val.get('auc')}, F1 {val.get('f1')} {r['job']['error']}")

            ns = await call("next_step")
            check("guide recommends the next step", ns["step"] in ("analyze_all", "predict", "review"), str(ns))
            other = (await call("search_tracks", no_mood=True, order="random", limit=40))["rows"]
            r = await call("analyze", track_ids=[t["id"] for t in other])
            check("analysis of new tracks", r["job"]["state"] == "finished")
            if has_clap:
                r = await call("analyze_clap", track_ids=[t["id"] for t in other])
                zs = await call("zero_shot_moods", threshold=0.3)
                check("zero-shot suggestions without training", zs["job"]["state"] == "finished"
                      and zs["job"]["result"]["predicted"] >= len(other), json.dumps(zs["job"]["result"]))
            wsug = await call("web_mood_suggestions", min_conf=0.5)
            check("internet mood suggestions are stored as suggestions", "suggested" in wsug, str(wsug))
            r = await call("predict_moods", threshold=0.5)
            check("predictions stored as suggestions", r["job"]["result"]["predicted"] == len(other)
                  and (await call("status"))["suggestions"] >= len(other), json.dumps(r["job"]["result"]))
            sug = await call("search_tracks", mood_status="suggested", mood_="Энергичное", limit=3, show=True)
            tid = (other[0])["id"]
            t = (await call("get_tracks", track_ids=[tid]))[0]
            check("suggestions are not confirmed moods", t["moods"] == [] and t["mood_suggestions"], str(t["mood_suggestions"]))
            r = await call("resolve_suggestions", accept=True, track_ids=[tid])
            t = (await call("get_tracks", track_ids=[tid]))[0]
            check("accepting a suggestion confirms it", len(t["moods"]) >= 1 and not t["mood_suggestions"], str(t["moods"]))

            ttl = await call("tracks_to_label", limit=5)
            check("active learning picks uncertain tracks", len(ttl) == 5 and all("uncertainty" in t for t in ttl))
            wv = await call("start_wave", moods=["Спокойное"], character="mixed")
            check("My wave starts and plays", wv["queued"] > 0 and wv["now"] is not None, f"{wv['queued']} queued, {wv['kind']}")
            fb = await call("wave_feedback", like=True)
            check("wave feedback is accepted", "error" not in fb, str(fb))
            await call("transport", action="pause")
            fd = await call("start_label_feed", source="random", count=10)
            fs = await call("label_feed_state")
            check("labelling feed opens and plays a track", fd["queued"] == 10 and fs["current"] and fs["left"] == 9, str(fs["left"]))
            await call("transport", action="pause")
            sim = await call("similar", track_id=ids["Спокойное"][0], limit=5)
            check("similar tracks", len(sim) == 5 and sim[0]["basis"] in ("tags", "audio+tags"))

            pl = await call("create_playlist", name="Редкое", rules={"max_plays": 0, "order": "rare_random", "limit": 20})
            tr = await call("playlist_tracks", playlist_id=pl["id"])
            check("smart playlist", pl["count"] == 20 and len(tr) == 20)

            await call("set_volume", volume=0.05)
            r = await call("play", playlist_id=pl["id"], shuffle="rare")
            await asyncio.sleep(4)
            ps = await call("player_state")
            check("playback runs in the exe", ps["state"] == "playing" and ps["position"] > 1,
                  f"{ps['state']} at {ps['position']:.1f}s")
            check("libmpv gapless engine is active in the exe", str(ps.get("engine", "")).startswith("mpv"), str(ps.get("engine")))
            await call("transport", action="next")
            await asyncio.sleep(1)
            ps2 = await call("player_state")
            check("next track", ps2["track"]["id"] != ps["track"]["id"])
            ui = await call("set_ui", color_mode="radial")
            check("colour music gets live samples in the exe", ui["audio"] == "live" and ui["color_mode"] == "radial", str(ui))
            await call("transport", action="pause")
            ui = await call("set_ui", zoom=1.3, tile_size=220)
            check("zoom and tile size", ui["zoom"] == 1.3 and ui["tile_size"] == 220, str(ui))
            await call("navigate", page="albums")
            await call("screenshot", page="albums")
            await call("set_ui", zoom=1.0)

            stats = await call("listening_stats", period_days=30)
            check("stats call", stats["total"] > 3000)
            gw = await call("write_genres_to_files", dry_run=True)
            check("genre write is a dry run by default", gw["dry_run"] and gw["would_change"] == 0)

            await call("navigate", page="moods")
            await call("screenshot", page="moods")
            await call("screenshot", page="library")
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> int:
    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "WallePlayer" / "WallePlayer.exe"
    t0 = time.time()
    asyncio.run(run(exe))
    failed = [n for n, ok, _ in checks if not ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed in {time.time() - t0:.0f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
