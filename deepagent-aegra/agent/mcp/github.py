"""GitHub-only helpers, reached through the per-server seam (`servers.py`).

Credentials are keyed by the pinned Registry Entry's field name
(docs/adr/0009): for GitHub's remote that is the `Authorization` header. The
person may paste a bare PAT or a full `Bearer <pat>` value; `_bearer`
normalises both.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx
from fastmcp.client.transports import StreamableHttpTransport

GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/"
GITHUB_USER_URL = "https://api.github.com/user"
PROBE_TIMEOUT = 15


class GitHubError(Exception):
    """Base for probe failures."""


class TokenRejected(GitHubError):
    """GitHub answered 401: the token is wrong, expired or revoked."""


class GitHubUnreachable(GitHubError):
    """GitHub could not be reached, or answered something unexpected."""


@dataclass(frozen=True)
class TokenInfo:
    login: str
    scopes: list[str]


def _bearer(value: str) -> str:
    value = value.strip()
    return value if value.lower().startswith("bearer ") else f"Bearer {value}"


async def probe_token(token: str, *, client: httpx.AsyncClient | None = None) -> TokenInfo:
    """`GET /user` with the token: who it belongs to and what it can do.

    Scopes come from `X-OAuth-Scopes` — empty when absent (fine-grained PATs
    don't send it). Pass `client` to inject a transport in tests.
    """
    headers = {"Authorization": _bearer(token), "Accept": "application/vnd.github+json"}
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as owned:
                response = await owned.get(GITHUB_USER_URL, headers=headers)
        else:
            response = await client.get(GITHUB_USER_URL, headers=headers)
    except httpx.HTTPError as exc:
        msg = f"could not reach api.github.com: {exc}"
        raise GitHubUnreachable(msg) from exc
    if response.status_code == 401:
        msg = "GitHub rejected the token (401)"
        raise TokenRejected(msg)
    if response.status_code != 200:
        msg = f"unexpected response from api.github.com: {response.status_code}"
        raise GitHubUnreachable(msg)
    raw = response.headers.get("X-OAuth-Scopes", "")
    scopes = [s.strip() for s in raw.split(",") if s.strip()]
    try:
        login = response.json()["login"]
    except (ValueError, KeyError, TypeError) as exc:
        msg = "api.github.com answered 200 without a usable `login`"
        raise GitHubUnreachable(msg) from exc
    return TokenInfo(login=login, scopes=scopes)


def build_transport(credentials: dict[str, str]) -> StreamableHttpTransport:
    """The remote streamable-http transport for a stored GitHub Connection."""
    return StreamableHttpTransport(
        GITHUB_MCP_URL, headers={"Authorization": _bearer(credentials["Authorization"])}
    )
