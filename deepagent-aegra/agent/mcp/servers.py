"""The per-server seam: everything that differs between curated MCP servers.

The settings API (`app.py`) and the `mcp` subagent (`subagent.py`) resolve a
server by slug from `SERVERS` and never call a server's own module directly. A
server supplies how to build its transport from a Connection's credentials, how
to validate them at save time, how to explain a connect failure, and its
init/request timeouts. GitHub and n8n implement it.

The credential fields a server needs are read from its Registry Entry
(`registry/<slug>.server.json`, docs/adr/0009); `credential_fields` also says
which are secret, so `redact` masks those and leaves e.g. n8n's URL readable.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from agent.mcp import github, n8n

_REGISTRY = Path(__file__).with_name("registry")


class CredentialsRejected(Exception):
    """Save-time validation failed; `status` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@cache
def credential_fields(slug: str) -> list[dict[str, Any]]:
    """The Credential Form fields of a server's Registry Entry: the remote's URL
    variables (the instance URL of a self-hosted server), then its headers."""
    entry = json.loads((_REGISTRY / f"{slug}.server.json").read_text())
    [remote] = [r for r in entry["remotes"] if r["type"] == "streamable-http"]
    # The Registry doesn't mark GitHub's token required on 1.12.2 (OAuth login is
    # possible interactively), but a headless deployment can only use a PAT; n8n's
    # entry marks both. Either way every field is required here.
    return [
        {
            "name": field["name"],
            "description": field.get("description", ""),
            "isRequired": True,
            "isSecret": field.get("isSecret", False),
        }
        for field in [
            *({"name": name, **var} for name, var in remote.get("variables", {}).items()),
            *remote.get("headers", []),
        ]
    ]


def secret_values(slug: str, credentials: dict[str, str]) -> dict[str, str]:
    """The stored credentials that are secret, per the Registry Entry."""
    secret = {f["name"] for f in credential_fields(slug) if f["isSecret"]}
    return {name: value for name, value in credentials.items() if name in secret}


def redact(text: str, secrets: dict[str, str]) -> str:
    """Mask every secret value (and a bare token after `Bearer `) in `text`."""
    for value in secrets.values():
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
        raise CredentialsRejected(502, redact(str(exc), secret_values("github", credentials))) from None
    return {"login": info.login, "scopes": info.scopes}


async def _github_failure_reason(credentials: dict[str, str], exc: Exception) -> str:
    """Re-run the token probe for a useful 401-vs-unreachable message."""
    try:
        await github.probe_token(credentials.get("Authorization", ""))
    except github.GitHubError as probe_exc:
        reason = str(probe_exc)
    else:
        reason = f"MCP connection failed: {type(exc).__name__}: {exc}"
    return redact(reason, secret_values("github", credentials))


async def _validate_n8n(credentials: dict[str, str]) -> dict[str, Any]:
    # No probe endpoint: a bad token surfaces as the MCP connect failing, not here.
    try:
        n8n.check_url(credentials["url"])
    except ValueError as exc:
        raise CredentialsRejected(422, str(exc)) from None
    return {}


async def _n8n_failure_reason(credentials: dict[str, str], exc: Exception) -> str:
    # The client reports a 401 as a generic protocol error, so a bad token and an
    # unreachable host read alike; the message names the instance (the URL is not secret).
    reason = f"MCP connection to {credentials.get('url', 'n8n')} failed: {type(exc).__name__}: {exc}"
    return redact(reason, secret_values("n8n", credentials))


# Both timeouts default to "no timeout" in fastmcp; a hung server must not hang a run.
SERVERS: dict[str, Server] = {
    "github": Server(
        build_transport=github.build_transport,
        validate=_validate_github,
        failure_reason=_github_failure_reason,
        unreachable_prefix="Token is valid but the GitHub MCP server could not be reached: ",
        init_timeout=30,
        request_timeout=60,
    ),
    "n8n": Server(
        build_transport=n8n.build_transport,
        validate=_validate_n8n,
        failure_reason=_n8n_failure_reason,
        unreachable_prefix="Could not connect to the n8n MCP server: ",
        init_timeout=30,
        request_timeout=300,  # workflow executions run inside a request
    ),
}
