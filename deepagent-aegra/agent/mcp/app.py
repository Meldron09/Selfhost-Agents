"""The `/mcp/connections` settings API behind agent-chat-ui's Settings surface.

A `FastAPI` router that `agent/files/app.py:create_app()` includes next to the
`/files` routes (aegra allows one `http.app`). Decisions: issue #25. No new
auth — it inherits the deployment's (none). Every handler is a plain `def`
(see agent/files/app.py for why). The router adds no middleware and logs
nothing: credentials pass through here.

Only curated servers exist: `github` and `n8n`. Their credential fields come
from vendored Registry Entries (registry/github.server.json =
`io.github.github/github-mcp-server@1.12.2`; registry/n8n.server.json is
hand-authored, n8n having no public listing), never from a runtime Registry
call. Per-server behaviour (transport, credential validation, error wording)
is resolved by slug in `servers.py`; the GitHub details below describe that
implementation, and n8n's differences are noted at the end.

API contract
------------
Errors are always `{"error": "<human-readable message>"}` — the frontend
shows it inline and never parses it. An unknown `{server}` slug is `404`.
Connection state is stored, never probed, except by `PUT …/credentials`.

`GET /mcp/connections` -> `200`, a list with one entry per curated server:

    {"server": "github", "title": str, "description": str,
     "credentialFields": [{"name": str, "description": str,
                           "isRequired": bool, "isSecret": bool}],
     "connection": null | {"enabled": bool, "login"?: str, "scopes"?: [str],
                           "values"?: {<non-secret field name>: str},
                           "toolCount": int, "lastError": str | null}}

`connection: null` means never connected. Secret values are never returned.
`login` and `scopes` are server-specific and may be absent. `values` carries
the stored non-secret fields (n8n's `url`) so the form can pre-fill them; it is
absent when a server has none (GitHub).

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

  Success: `200 {"login"?: str, "scopes"?: [str], "toolCount": int,
  "enabled": bool}`. The first Connect saves `enabled: true`; connecting again
  replaces the credentials, preserves `enabled` and clears `lastError`. A
  failed re-submit leaves the existing Connection untouched.

`PATCH /mcp/connections/{server}` — body `{"enabled": bool}` (strict boolean;
anything else `422`) -> `200 {"enabled": bool}`; `404` if never connected.

`DELETE /mcp/connections/{server}` — removes credentials and state;
idempotent `204` whether or not a Connection existed.

n8n (`PUT …/n8n/credentials`, body `{"url": str, "Authorization": str}`): a
`url` that is not an absolute http(s) URL is `422` before any connection; there
is no probe, so a rejected token and an unreachable host are both the `502` of
check 3 (`"Could not connect to the n8n MCP server: …"`), redacting only the
token. Success is `200 {"toolCount": int, "enabled": bool}`, with no
`login`/`scopes`.

A Connection Store failure (e.g. `MCP_STORE_KEY` unset, wrong key, unwritable
volume) is `500` with the underlying message.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastmcp import Client
from pydantic import BaseModel, StrictBool, StrictStr, ValidationError, create_model
from starlette.responses import Response

from agent.mcp import store
from agent.mcp.servers import SERVERS, CredentialsRejected, credential_fields, redact, secret_values

# Both default to "no timeout" in fastmcp; a hung MCP server must not hang the request.
MCP_TIMEOUT = 20

_CURATED: dict[str, dict[str, str]] = {
    "github": {
        "title": "GitHub",
        "description": "Search code and work with repositories, issues and pull requests.",
    },
    "n8n": {
        "title": "n8n",
        "description": "Find, create and run workflows on your n8n instance.",
    },
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


def _server(slug: str) -> dict[str, str]:
    if slug not in _CURATED:
        raise ApiError(404, f"Unknown MCP server {slug!r}")
    return _CURATED[slug]


def _parse_credentials(slug: str, body: Any) -> dict[str, str]:
    if not isinstance(body, dict):
        raise ApiError(422, "Credentials must be a JSON object")
    fields = {f["name"]: (StrictStr, ...) for f in credential_fields(slug)}
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


async def _validate(slug: str, credentials: dict[str, str]) -> tuple[dict[str, Any], int]:
    seam = SERVERS[slug]
    try:
        extra = await seam.validate(credentials)
    except CredentialsRejected as exc:
        raise ApiError(exc.status, exc.message) from None
    try:
        async with Client(
            seam.build_transport(credentials), init_timeout=MCP_TIMEOUT, timeout=MCP_TIMEOUT
        ) as client:
            tool_count = len(await client.list_tools())
    except Exception as exc:  # noqa: BLE001 — whatever the MCP client raises is "unreachable"
        message = redact(str(exc) or type(exc).__name__, secret_values(slug, credentials))
        raise ApiError(502, f"{seam.unreachable_prefix}{message}") from None
    return extra, tool_count


def _public_values(connection: dict[str, Any], public: set[str]) -> dict[str, Any]:
    """Non-secret credential values (n8n's URL) so the form can pre-fill them."""
    values = {k: v for k, v in connection["credentials"].items() if k in public}
    return {"values": values} if values else {}


def list_connections() -> list[dict[str, Any]]:
    stored = store.load()
    entries = []
    for slug, server in _CURATED.items():
        connection = stored.get(slug)
        public = {f["name"] for f in credential_fields(slug) if not f["isSecret"]}
        entries.append(
            {
                "server": slug,
                "title": server["title"],
                "description": server["description"],
                "credentialFields": credential_fields(slug),
                "connection": connection
                and {
                    key: connection[key]
                    for key in ("enabled", "login", "scopes", "toolCount", "lastError")
                    if key in connection
                }
                | _public_values(connection, public),
            }
        )
    return entries


def put_credentials(server: str, body: Any = Body(None)) -> dict[str, Any]:
    _server(server)
    credentials = _parse_credentials(server, body)
    # A sync handler runs in Starlette's threadpool, so there is no running loop here.
    extra, tool_count = asyncio.run(_validate(server, credentials))
    enabled = store.save_connection(server, credentials, tool_count=tool_count, enabled=None, **extra)
    return {**extra, "toolCount": tool_count, "enabled": enabled}


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
