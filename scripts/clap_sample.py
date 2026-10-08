"""Embed a random sample of unlabelled tracks via MCP (demo helper): python scripts/clap_sample.py [N] [--port 8766]"""
import asyncio
import json
import sys

from mcp import Client


async def main(n: int, port: int) -> None:
    async with Client(f"http://127.0.0.1:{port}/mcp", read_timeout_seconds=None) as c:
        r = await c.call_tool("search_tracks", {"no_mood": True, "order": "random", "limit": n})
        data = lambda x: x.structured_content or json.loads(x.content[0].text)  # noqa: E731
        ids = [t["id"] for t in data(r)["rows"]]
        r = await c.call_tool("analyze_clap", {"track_ids": ids})
        j = data(r)["job"]
        sys.stdout.reconfigure(encoding="utf-8")
        print(j["state"], j["result"], j["error"])


n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 600
port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8766
asyncio.run(main(n, port))
