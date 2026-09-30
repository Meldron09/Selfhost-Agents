"""n8n helpers, reached through the per-server seam (`servers.py`).

n8n is a self-hosted instance, so its MCP URL is a stored credential (the
Registry Entry's `url` variable) rather than a constant. There is no probe
endpoint: validation is the MCP connect + `list_tools()` the settings API
already does.
"""
from __future__ import annotations

from urllib.parse import urlparse

from fastmcp.client.transports import StreamableHttpTransport

from agent.mcp.http import build_transport as _transport


def check_url(url: str) -> None:
    """Raise `ValueError` unless `url` is an absolute http(s) URL.

    Plain `http://` is fine: a self-hosted instance may sit on a LAN.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        msg = "url: must be an absolute http:// or https:// URL"
        raise ValueError(msg)


def build_transport(credentials: dict[str, str]) -> StreamableHttpTransport:
    return _transport(credentials["url"], credentials["Authorization"])
