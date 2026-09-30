"""The per-server seam: everything that differs between curated MCP servers.

The settings API (`app.py`) and the `mcp` subagent (`subagent.py`) resolve a
server by slug from `SERVERS` and never call a server's own module directly. A
server supplies how to build its transport from a Connection's credentials, how
to validate them at save time, how to explain a connect failure, and its
init/request timeouts. GitHub is the only implementation today.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agent.mcp import github


class CredentialsRejected(Exception):
    """Save-time validation failed; `status` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def redact(text: str, credentials: dict[str, str]) -> str:
    """Mask every stored credential value (and a bare token after `Bearer `) in `text`."""
    for value in credentials.values():
        for secret in {value, value.split()[-1]}:
            text = text.replace(secret, "***")
    return text


@dataclass
class Server:
    build_transport: Callable[[dict[str, str]], Any]
    # Checks the credentials before the MCP connect; returns extra fields to store
    # (e.g. `login`, `scopes`) or raises `CredentialsRejected`.
    validate: Callable[[dict[str, str]], Awaitable[dict[str, Any]]]
    # Why a connect failed, redacted: shown to the person and persisted as `lastError`.
    failure_reason: Callable[[dict[str, str], Exception], Awaitable[str]]
    # Prefix of the save-time error when validation passed but `list_tools()` failed.
    unreachable_prefix: str
    init_timeout: int
    request_timeout: int


async def _validate_github(credentials: dict[str, str]) -> dict[str, Any]:
    try:
        info = await github.probe_token(credentials["Authorization"])
    except github.TokenRejected:
        raise CredentialsRejected(422, "GitHub rejected this token (401 Bad credentials)") from None
    except github.GitHubError as exc:
        raise CredentialsRejected(502, redact(str(exc), credentials)) from None
    return {"login": info.login, "scopes": info.scopes}


async def _github_failure_reason(credentials: dict[str, str], exc: Exception) -> str:
    """Re-run the token probe for a useful 401-vs-unreachable message."""
    try:
        await github.probe_token(credentials.get("Authorization", ""))
    except github.GitHubError as probe_exc:
        reason = str(probe_exc)
    else:
        reason = f"MCP connection failed: {type(exc).__name__}: {exc}"
    return redact(reason, credentials)


# Both timeouts default to "no timeout" in fastmcp; a hung server must not hang a run.
SERVERS: dict[str, Server] = {
    "github": Server(
        build_transport=github.build_transport,
        validate=_validate_github,
        failure_reason=_github_failure_reason,
        unreachable_prefix="Token is valid but the GitHub MCP server could not be reached: ",
        init_timeout=30,
        request_timeout=60,
    )
}
