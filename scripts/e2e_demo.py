"""Live end-to-end demo against the RUNNING app (python -m musicplayer, ideally with MUSICPLAYER_HOME set to a
sandbox): labels some tracks the way Claude would over MCP, analyses audio, trains the mood model, predicts,
accepts suggestions - the app window updates at every step.

    python scripts/e2e_demo.py [--port 8766] [--per-artist 8]
"""

from __future__ import annotations

import asyncio
import json
import sys

from mcp import Client

# DEMO labels only (sandbox): artists whose sound is clearly different, to give the model a learnable signal.
DEMO = {
    "Злое": ["Architects", "ERRA", "Soilwork", "Wage War", "System of a Down"],
    "Спокойное": ["Carbon Based Lifeforms", "Ella Fitzgerald", "Simon & Garfunkel", "Moby", "Ingrid Michaelson"],
    "Энергичное": ["Yellowcard", "Sum 41", "Story of the Year", "Fall Out Boy", "Papa Roach"],
    "Грустное": ["Flatsound", "Trevor Hall", "Karīna Gretere", "She & Him", "Michale Graves"],
}


def out(*a) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print(*a, flush=True)


async def main(port: int, per_artist: int) -> None:
    async with Client(f"http://127.0.0.1:{port}/mcp", read_timeout_seconds=None) as c:
        async def call(tool: str, **kw):
            r = await c.call_tool(tool, kw)
            if r.structured_content:
                sc = r.structured_content
                return sc["result"] if list(sc) == ["result"] else sc
            text = [b.text for b in r.content if getattr(b, "text", None) is not None]
            return json.loads(text[0]) if text else {}

        st = await call("status")
        out("Состояние:", st["tracks"], "треков, с настроениями", st["with_moods"], "проанализировано", st["analyzed"])
        if st["tracks"] == 0:
            out("Сканирую библиотеку…")
            await call("scan")

        await call("navigate", page="library")
        labelled: dict[str, list[int]] = {}
        for mood, artists in DEMO.items():
            ids: list[int] = []
            for a in artists:
                res = await call("search_tracks", artist=a, order="random", limit=per_artist, show=True)
                ids += [r["id"] for r in res["rows"]]
                await asyncio.sleep(0.4)
            await call("set_moods", track_ids=ids, moods=[mood], mode="set")
            labelled[mood] = ids
            out(f"Разметил «{mood}»: {len(ids)} треков")
        await call("search_tracks", text="", show=True)

        out("Анализ звука (дескрипторы Freesound)…")
        await call("navigate", page="moods")
        r = await call("analyze", scope="labeled")
        out("  ", r["job"]["state"], r["job"]["result"])

        out("Обучение…")
        r = await call("train_model", epochs=150)
        out("  ", r["job"]["state"], json.dumps(r["job"]["result"], ensure_ascii=False)[:300])

        out("Предсказание: анализирую 120 случайных неразмеченных треков и предсказываю им настроение…")
        others = await call("search_tracks", no_mood=True, order="random", limit=120)
        r = await call("analyze", track_ids=[t["id"] for t in others["rows"]])
        out("  ", r["job"]["state"], r["job"]["result"])
        r = await call("predict_moods", threshold=0.5)
        out("  ", r["job"]["state"], r["job"]["result"])
        sug = await call("search_tracks", mood_status="suggested", mood_="Злое", limit=5)
        out("Предложено «Злое»:", [(t["artist"], t["title"], t["mood_suggestions"]) for t in sug["rows"]])
        await call("screenshot", page="moods")
        out("Готово")


if __name__ == "__main__":
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1]) if "--port" in args else 8766
    per = int(args[args.index("--per-artist") + 1]) if "--per-artist" in args else 8
    asyncio.run(main(port, per))
