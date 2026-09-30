"""The `/mcp/connections` settings API behind agent-chat-ui's Settings surface.

A `FastAPI` router that `agent/files/app.py:create_app()` includes next to the
`/files` routes (aegra allows one `http.app`). Decisions: issue #25. No new
auth — it inherits the deployment's (none). Every handler is a plain `def`
(see agent/files/app.py for why). The router adds no middleware and logs
nothing: credentials pass through here.

Only curated servers exist; today that is `github`, whose credential fields
come from the vendored Registry Entry snapshot
(registry/github.server.json = `io.github.github/github-mcp-server@1.12.2`),
never from a runtime Registry call.

API contract
------------
Errors are always `{"error": "<human-readable message>"}` — the frontend
shows it inline and never parses it. An unknown `{server}` slug is `404`.
Connection state is stored, never probed, except by `PUT …/credentials`.

`GET /mcp/connections` -> `200`, a list with one entry per curated server:

    {"server": "github", "title": str, "description": str,
     "credentialFields": [{"name": str, "description": str,
                           "isRequired": bool, "isSecret": bool}],
     "connection": null | {"enabled": bool, "login": str, "scopes": [str],
                           "toolCount": int, "lastError": str | null}}

`connection: null` means never connected. Secret values are never returned.

`PUT /mcp/connections/{server}/credentials` — validate-and-save. Body: a JSON
object `{<credentialFields name>: <non-empty string>}` (unknown keys are
ignored). Checks, in order; only if all pass are the credentials persisted:

  1. malformed/missing fields                        -> `422`
  2. `GET api.github.com/user` with the token:
     GitHub answers 401                              -> `422
        {"error": "GitHub rejected this token (401 Bad credentials)"}`
     api.github.com unreachable / unexpected answer  -> `502`
  3. connect to the GitHub MCP server + `list_tools`,
     with explicit timeouts, fails                   -> `502
        {"error": "Token is valid but the GitHub MCP server could not be
                   reached: …"}`

  Success: `200 {"login": str, "scopes": [str], "toolCount": int,
  "enabled": bool}`. The first Connect saves `enabled: true`; connecting again
  replaces the credentials, preserves `enabled` and clears `lastError`. A
  failed re-submit leaves the existing Connection untouched.

`PATCH /mcp/connections/{server}` — body `{"enabled": bool}` (strict boolean;
anything else `422`) -> `200 {"enabled": bool}`; `404` if never connected.

`DELETE /mcp/connections/{server}` — removes credentials and state;
idempotent `204` whether or not a Connection existed.

A Connection Store failure (e.g. `MCP_STORE_KEY` unset, wrong key, unwritable
volume) is `500` with the underlying message.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastmcp import Client
from pydantic import BaseModel, StrictBool, StrictStr, ValidationError, create_model
from starlette.responses import Response

from agent.mcp import store
from agent.mcp.github import GitHubError, TokenRejected, build_transport, probe_token

# Both default to "no timeout" in fastmcp; a hung MCP server must not hang the request.
MCP_TIMEOUT = 20

_REGISTRY = Path(__file__).with_name("registry")

_CURATED: dict[str, dict[str, Any]] = {
    "github": {
        "title": "GitHub",
        "description": "Search code and work with repositories, issues and pull requests.",
        "entry": json.loads((_REGISTRY / "github.server.json").read_text()),
    }
}


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class _JsonErrorRoute(APIRoute):
    """Turns every failure this router knows about into `{"error": message}`."""

    def get_route_handler(self):
        handle = super().get_route_handler()

        async def handler(request):
            try:
                return await handle(request)
            except ApiError as exc:
                return JSONResponse({"error": exc.message}, status_code=exc.status)
            except (store.StoreError, OSError) as exc:
                return JSONResponse({"error": str(exc)}, status_code=500)
            except RequestValidationError as exc:
                # loc + msg only: `input` would echo the submitted secret back.
                return JSONResponse({"error": _format_errors(exc.errors())}, status_code=422)

        return handler


router = APIRouter(prefix="/mcp/connections", route_class=_JsonErrorRoute)


def _format_errors(errors: Any) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc'] if p != 'body') or 'body'}: {e['msg']}" for e in errors
    )


def _server(slug: str) -> dict[str, Any]:
    if slug not in _CURATED:
        raise ApiError(404, f"Unknown MCP server {slug!r}")
    return _CURATED[slug]


def _credential_fields(server: dict[str, Any]) -> list[dict[str, Any]]:
    [remote] = [r for r in server["entry"]["remotes"] if r["type"] == "streamable-http"]
    return [
        {
            "name": header["name"],
            "description": header.get("description", ""),
            # The Registry doesn't mark the token required on 1.12.2 (OAuth login is
            # possible interactively), but a headless deployment can only use a PAT.
            "isRequired": True,
            "isSecret": header.get("isSecret", False),
        }
        for header in remote["headers"]
    ]


def _redact(text: str, credentials: dict[str, str]) -> str:
    for value in credentials.values():
        for secret in {value, value.split()[-1]}:
            text = text.replace(secret, "***")
    return text


def _parse_credentials(server: dict[str, Any], body: Any) -> dict[str, str]:
    if not isinstance(body, dict):
        raise ApiError(422, "Credentials must be a JSON object")
    fields = {f["name"]: (StrictStr, ...) for f in _credential_fields(server)}
    schema = create_model("Credentials", **fields)
    try:
        parsed = schema.model_validate(body)
    except ValidationError as exc:
        raise ApiError(422, _format_errors(exc.errors())) from None
    credentials = parsed.model_dump()
    for name, value in credentials.items():
        if not value.strip():
            raise ApiError(422, f"{name}: must not be empty")
    return credentials


async def _validate(credentials: dict[str, str]) -> tuple[Any, int]:
    token = credentials["Authorization"]
    try:
        info = await probe_token(token)
    except TokenRejected:
        raise ApiError(422, "GitHub rejected this token (401 Bad credentials)") from None
    except GitHubError as exc:
        raise ApiError(502, _redact(str(exc), credentials)) from None
    try:
        async with Client(build_transport(credentials), init_timeout=MCP_TIMEOUT, timeout=MCP_TIMEOUT) as client:
            tool_count = len(await client.list_tools())
    except Exception as exc:  # noqa: BLE001 — whatever the MCP client raises is "unreachable"
        message = _redact(str(exc) or type(exc).__name__, credentials)
        raise ApiError(
            502, f"Token is valid but the GitHub MCP server could not be reached: {message}"
        ) from None
    return info, tool_count


def list_connections() -> list[dict[str, Any]]:
    stored = store.load()
    entries = []
    for slug, server in _CURATED.items():
        connection = stored.get(slug)
        entries.append(
            {
                "server": slug,
                "title": server["title"],
                "description": server["description"],
                "credentialFields": _credential_fields(server),
                "connection": connection
                and {
                    key: connection[key]
                    for key in ("enabled", "login", "scopes", "toolCount", "lastError")
                },
            }
        )
    return entries


def put_credentials(server: str, body: Any = Body(None)) -> dict[str, Any]:
    curated = _server(server)
    credentials = _parse_credentials(curated, body)
    # A sync handler runs in Starlette's threadpool, so there is no running loop here.
    info, tool_count = asyncio.run(_validate(credentials))
    enabled = store.save_connection(
        server, credentials, login=info.login, scopes=info.scopes, tool_count=tool_count, enabled=None
    )
    return {"login": info.login, "scopes": info.scopes, "toolCount": tool_count, "enabled": enabled}


class _EnabledBody(BaseModel):
    enabled: StrictBool


def patch_connection(server: str, body: _EnabledBody) -> dict[str, bool]:
    _server(server)
    try:
        store.set_enabled(server, body.enabled)
    except KeyError:
        raise ApiError(404, "Not connected — connect first") from None
    return {"enabled": body.enabled}


def delete_connection(server: str) -> Response:
    _server(server)
    store.delete(server)
    return Response(status_code=204)


router.add_api_route("", list_connections, methods=["GET"])
router.add_api_route("/{server}/credentials", put_credentials, methods=["PUT"])
router.add_api_route("/{server}", patch_connection, methods=["PATCH"])
router.add_api_route("/{server}", delete_connection, methods=["DELETE"])
