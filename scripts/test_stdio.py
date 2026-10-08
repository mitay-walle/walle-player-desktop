"""Check the stdio proxy: python scripts/test_stdio.py  (starts the app itself if it is not running)."""
import asyncio
import json
import sys

from mcp import Client, StdioServerParameters

ROOT = __file__.rsplit("scripts", 1)[0]


async def main() -> int:
    params = StdioServerParameters(command=ROOT + ".venv\Scripts\python.exe", args=["-m", "musicplayer.mcp_stdio"], cwd=ROOT,
                                 env={"MUSICPLAYER_HOME": ROOT + "data\e2e", "MUSICPLAYER_EXE": ROOT + "dist\WallePlayer\WallePlayer.exe",
                                      "PYTHONIOENCODING": "utf-8"})
    async with Client(params, read_timeout_seconds=120) as c:
        tools = await c.list_tools()
        names = [t.name for t in tools.tools]
        r = await c.call_tool("status", {})
        data = lambda res: res.structured_content or json.loads(res.content[0].text)  # noqa: E731
        st = data(r)
        r2 = data(await c.call_tool("search_tracks", {"text": "король", "limit": 2}))
        ok = len(names) > 30 and "tracks" in st and r2["total"] > 0
        sys.stdout.reconfigure(encoding="utf-8")
        print(("PASS" if ok else "FAIL"), len(names), "tools,", st["tracks"], "tracks,", r2["total"], "hits")
        return 0 if ok else 1


sys.exit(asyncio.run(main()))
