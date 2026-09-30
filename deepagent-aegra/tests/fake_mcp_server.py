"""A local streamable-HTTP MCP server for graph tests (no network, no GitHub).

Tools: `read_thing` (readOnlyHint=True) and `write_thing` (False). `SEEN_AUTH`
records each POST's Authorization header.
"""
from __future__ import annotations

import socket
import threading
import time

import uvicorn
from fastmcp import FastMCP

SEEN_AUTH: list[str | None] = []


def _build() -> FastMCP:
    mcp = FastMCP("fake")

    @mcp.tool(annotations={"readOnlyHint": True})
    def read_thing(name: str) -> str:
        return f"thing:{name}"

    @mcp.tool(annotations={"readOnlyHint": False})
    def write_thing(name: str) -> str:
        return f"wrote:{name}"

    return mcp


class _Spy:
    """Pure-ASGI wrapper recording request headers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "POST":
            headers = dict(scope["headers"])
            SEEN_AUTH.append(headers.get(b"authorization", b"").decode() or None)
        return await self.app(scope, receive, send)


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def start() -> str:
    """Start the server on a free loopback port; return its /mcp URL."""
    port = _free_port()
    app = _Spy(_build().http_app(path="/mcp"))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}/mcp"


def dead_url() -> str:
    """A loopback URL with nothing listening."""
    return f"http://127.0.0.1:{_free_port()}/mcp"
