"""MCP over stdio: a thin proxy to the running app's HTTP server (python -m musicplayer.mcp_stdio).

Claude Desktop / Claude Code can start this as a stdio MCP server.  If the app is not running yet it is
launched first (MUSICPLAYER_EXE, else dist\\WallePlayer\\WallePlayer.exe next to the project, else
``python -m musicplayer``) and the proxy waits for its port.  All tools live in the app, so the window shows
everything Claude does and there is a single player/state.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path

from mcp import Client, types
from mcp.server import Server
from mcp.server.stdio import stdio_server

PORT = int(os.environ.get("MUSICPLAYER_MCP_PORT", "8766"))
URL = f"http://127.0.0.1:{PORT}/mcp"


def port_open() -> bool:
    try:
        socket.create_connection(("127.0.0.1", PORT), 0.5).close()
        return True
    except OSError:
        return False


def launch_app() -> None:
    root = Path(__file__).resolve().parent.parent
    exe = os.environ.get("MUSICPLAYER_EXE") or str(root / "dist" / "WallePlayer" / "WallePlayer.exe")
    if Path(exe).exists():
        cmd, cwd = [exe], str(Path(exe).parent)
    else:
        py = root / ".venv" / "Scripts" / "pythonw.exe"
        cmd, cwd = [str(py if py.exists() else sys.executable), "-m", "musicplayer"], str(root)
    subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def ensure_app(timeout: float = 90) -> None:
    if port_open():
        return
    launch_app()
    for _ in range(int(timeout * 2)):
        if port_open():
            await asyncio.sleep(0.5)
            return
        await asyncio.sleep(0.5)
    raise RuntimeError(f"Walle Player did not start (port {PORT})")


async def main() -> None:
    await ensure_app()
    async with Client(URL, read_timeout_seconds=None) as app:
        info = await app.list_tools()

        async def on_list_tools(ctx, params) -> types.ListToolsResult:
            return await app.list_tools()

        async def on_call_tool(ctx, params: types.CallToolParams) -> types.CallToolResult:
            if not port_open():                       # the app was closed meanwhile
                raise RuntimeError("Walle Player is not running")
            return await app.call_tool(params.name, params.arguments or {})

        server = Server("music-player-stdio", instructions="Proxy to the running Walle Player app.",
                        on_list_tools=on_list_tools, on_call_tool=on_call_tool)
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
        del info


if __name__ == "__main__":
    asyncio.run(main())
