"""The streamable-HTTP transport shared by the curated servers: a URL plus a
token sent as `Authorization`. The person may paste a bare token or a full
`Bearer <token>` value; `bearer` normalises both.
"""
from __future__ import annotations

from fastmcp.client.transports import StreamableHttpTransport


def bearer(value: str) -> str:
    value = value.strip()
    return value if value.lower().startswith("bearer ") else f"Bearer {value}"


def build_transport(url: str, token: str) -> StreamableHttpTransport:
    return StreamableHttpTransport(url, headers={"Authorization": bearer(token)})
