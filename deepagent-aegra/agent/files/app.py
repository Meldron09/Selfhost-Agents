"""The upload/download HTTP app: `/files` routes over local disk.

docs/adr/0004-upload-download-http-app-route-contract.md is the route
contract this implements. Structurally mirrors `agent-runtime`'s read-only
`agent/artifacts/app.py` for the same reasons, both still true here even
though this app also writes:

* **Every handler is a plain `def`, never `async def`.** Once mounted, routes
  run on the agent's own event loop (`aegra`) — Starlette only moves a `def`
  handler's blocking file I/O off that loop via a threadpool; an `async def`
  handler doing the same disk read/write would stall every concurrent agent
  run behind an upload or download.
* **This app adds no middleware of its own.** Mounted unprefixed at the app
  root, its middleware would otherwise run for every route the mounting
  server owns, including the agent's own API.

Built with `FastAPI`, not plain `Starlette`, for the same reason as
`agent/artifacts/app.py`: `aegra`'s `http.app` loader
(`aegra_api.core.app_loader.load_custom_app`) requires the target to already
be a `FastAPI` instance, and `UploadFile`/`File(...)` is how a plain `def`
handler gets multipart form data parsed for it rather than having to
`await request.form()` itself (which a sync handler can't do).
"""
from __future__ import annotations

import mimetypes

from fastapi import FastAPI, File, UploadFile
from starlette.responses import Response

from agent.config import get_settings
from agent.files.store import load, save


def upload_file(file: UploadFile = File(...)) -> dict[str, str]:
    """`POST /files` — store the uploaded bytes; returns `{"key": "..."}`."""
    settings = get_settings()
    data = file.file.read()
    key = save(settings.file_store_dir, file.filename or "", data)
    return {"key": key}


def download_file(key: str) -> Response:
    """`GET /files/{key}` — the stored bytes, or 404 if `key` doesn't resolve.

    A plain empty-body `Response`, not `HTTPException` — mirrors
    `agent-runtime`'s `download_artifact` exactly, and keeps this handler's
    `-> Response` return type honest on every path instead of relying on
    FastAPI's default exception handler to turn a raised `HTTPException`
    into a JSON body on the 404 path alone.
    """
    settings = get_settings()
    try:
        data = load(settings.file_store_dir, key)
    except KeyError:
        return Response(status_code=404)

    content_type, _ = mimetypes.guess_type(key)
    return Response(
        content=data,
        media_type=content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{key}"'},
    )


def create_app() -> FastAPI:
    """A fresh app instance with no middleware — for standalone serving, tests,
    or mounting into `aegra` (whose `http.app` loader requires a `FastAPI`
    instance specifically — see aegra-host/http_app_adapter.py). Docs/OpenAPI
    routes are disabled: FastAPI registers them as `async def` handlers,
    which would trip `test_no_route_handler_is_async`, and this app has no
    use for a docs UI.
    """
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_api_route("/files", upload_file, methods=["POST"])
    app.add_api_route("/files/{key}", download_file, methods=["GET"])
    return app


app = create_app()
"""The mountable ASGI app — `agent/files/app.py:app`."""
