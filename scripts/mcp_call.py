"""Call a tool of the running app's MCP server: python scripts/mcp_call.py <tool> [key=value ...] [--port 8766]

Screenshots (image results) are saved to data/shot.png.  Prints the structured result as JSON.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

from mcp import Client


async def call(tool: str, args: dict, port: int = 8766):
    async with Client(f"http://127.0.0.1:{port}/mcp", read_timeout_seconds=None) as c:
        return await c.call_tool(tool, args)


def main() -> None:
    argv = sys.argv[1:]
    port = 8766
    if "--port" in argv:
        i = argv.index("--port")
        port = int(argv[i + 1])
        del argv[i:i + 2]
    tool = argv[0]
    args: dict = {}
    for a in argv[1:]:
        if a.startswith("{"):
            args.update(json.loads(a))
        else:  # key=value, value parsed as JSON when possible (PowerShell eats quotes in raw JSON)
            k, _, v = a.partition("=")
            try:
                args[k] = json.loads(v)
            except ValueError:
                args[k] = v
    res = asyncio.run(call(tool, args, port))
    out = []
    for block in res.content:
        if getattr(block, "type", "") == "image":
            p = Path(__file__).resolve().parent.parent / "data" / "shot.png"
            p.parent.mkdir(exist_ok=True)
            p.write_bytes(base64.b64decode(block.data))
            out.append(f"[image saved to {p}]")
        elif getattr(block, "text", None) is not None:
            out.append(block.text)
    sys.stdout.reconfigure(encoding="utf-8")
    print("\n".join(out))
    if res.is_error:
        sys.exit(1)


if __name__ == "__main__":
    main()
