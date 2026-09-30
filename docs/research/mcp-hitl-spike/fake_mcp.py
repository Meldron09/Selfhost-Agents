"""Local fake MCP server (streamable HTTP, background thread) that counts JSON-RPC methods.

Tools: `read_thing` (readOnlyHint=True), `write_thing` (readOnlyHint=False),
`mystery` (no annotations at all), `delete_thing` (destructive write, annotated False).
`COUNTS["initialize"]` == number of client connects; `COUNTS["tools/list"]` == list_tools calls;
`CALLS` records executed tool names (proves whether a gated write actually ran).
"""
from __future__ import annotations

import collections
import json
import socket
import threading
import time

import uvicorn
from fastmcp import FastMCP

COUNTS: collections.Counter = collections.Counter()
CALLS: list[str] = []
SEEN_AUTH: list[str | None] = []


def reset() -> None:
    COUNTS.clear()
    CALLS.clear()
    SEEN_AUTH.clear()


def build(delete_read_only: bool = False) -> FastMCP:
    mcp = FastMCP("fake")

    @mcp.tool(annotations={"readOnlyHint": True})
    def read_thing(name: str) -> str:
        CALLS.append("read_thing")
        return f"thing:{name}"

    @mcp.tool(annotations={"readOnlyHint": False})
    def write_thing(name: str) -> str:
        CALLS.append("write_thing")
        return f"wrote:{name}"

    @mcp.tool()
    def mystery(name: str) -> str:
        CALLS.append("mystery")
        return f"mystery:{name}"

    @mcp.tool(annotations={"readOnlyHint": delete_read_only, "destructiveHint": not delete_read_only})
    def delete_thing(name: str) -> str:
        CALLS.append("delete_thing")
        return f"deleted:{name}"

    return mcp


class _Counter:
    """Pure-ASGI wrapper: counts JSON-RPC `method`s in POST bodies."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] == "POST":
            body = b""
            more = True
            while more:
                msg = await receive()
                body += msg.get("body", b"")
                more = msg.get("more_body", False)
            try:
                payload = json.loads(body)
                for p in payload if isinstance(payload, list) else [payload]:
                    if "method" in p:
                        COUNTS[p["method"]] += 1
            except Exception:
                pass
            hdrs = dict(scope["headers"])
            SEEN_AUTH.append(hdrs.get(b"authorization", b"").decode() or None)
            sent = False

            async def replay():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            return await self.app(scope, replay, send)
        return await self.app(scope, receive, send)


def start(delete_read_only: bool = False) -> str:
    """Start the server on a free port; return its /mcp URL."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    app = _Counter(build(delete_read_only).http_app(path="/mcp"))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}/mcp"
