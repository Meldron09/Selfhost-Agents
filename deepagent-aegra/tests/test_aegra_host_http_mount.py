"""The upload/download app's structural guarantees, re-verified once actually
mounted into `aegra`'s own ASGI app — not only in isolation (test_files_app.py
covers the standalone app).

Ported near-verbatim from `agent-runtime/tests/test_aegra_host_http_mount.py`
(docs/adr/0005-testing-strategy-carryover-from-agent-runtime.md): same
`aegra_api.main.create_app()` shape, same `http_app_adapter.py`, only the
mounted target changes (`agent/files/app.py:create_app`, `/files` instead of
`/artifacts`).

Only `aegra_api.main.create_app()` is called — the FastAPI object is
inspected directly, never served, so this needs no live Postgres, no live
`aegra` process, no network.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
AEGRA_HOST = REPO_ROOT / "aegra-host"


@pytest.fixture
def merged_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`aegra`'s own app, with the files app mounted exactly as `aegra-host/`
    configures it — the adapter and a minimal `aegra.json` sitting side by
    side, the same layout `entrypoint.sh` renders inside the real container.
    """
    (tmp_path / "http_app_adapter.py").write_text((AEGRA_HOST / "http_app_adapter.py").read_text())
    (tmp_path / "aegra.json").write_text(json.dumps({"http": {"app": "./http_app_adapter.py:app"}}))

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AEGRA_HTTP_APP_DEPENDENCY_PATH", str(REPO_ROOT))
    monkeypatch.setenv("AEGRA_HTTP_APP_TARGET", "agent/files/app.py:create_app")
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", "32768")
    monkeypatch.setenv("FILE_STORE_DIR", str(tmp_path / "data"))

    import aegra_api.main as aegra_main

    return aegra_main.create_app()


def _flattened_routes(routes):
    """`app.routes` entries for a router included via `include_router` (every
    aegra core router: health/assistants/threads/runs/store) come back as a
    lazy `_IncludedRouter` wrapper in this FastAPI version, not a flat `Route`
    — unwrap it via its `original_router` to see the routes it actually holds.
    Our own `/files` routes, registered straight onto the app, already come
    back as plain routes and pass through untouched.
    """
    flat = []
    for route in routes:
        if hasattr(route, "path"):
            flat.append(route)
        elif hasattr(route, "original_router"):
            flat.extend(_flattened_routes(route.original_router.routes))
    return flat


def _files_routes(app):
    return [route for route in _flattened_routes(app.routes) if route.path.startswith("/files")]


def test_the_files_routes_are_present_once_mounted(merged_app):
    paths = {route.path for route in _files_routes(merged_app)}
    assert paths == {"/files", "/files/{key}"}


def test_the_mounted_files_routes_keep_their_own_methods(merged_app):
    routes = {route.path: route.methods for route in _files_routes(merged_app)}
    assert "POST" in routes["/files"]
    assert "GET" in routes["/files/{key}"]


def test_the_mounted_files_routes_still_have_sync_handlers(merged_app):
    routes = _files_routes(merged_app)
    assert routes
    for route in routes:
        assert not inspect.iscoroutinefunction(route.endpoint), route.path


def test_the_agents_own_routes_are_unaffected_by_the_mount(merged_app):
    """Threads/runs (the agent's own API) still work once `/files` joins them."""
    paths = {route.path for route in _flattened_routes(merged_app.routes)}
    assert "/threads" in paths
    assert "/threads/{thread_id}" in paths


def test_files_does_not_collide_with_any_of_aegras_own_routes(merged_app):
    """Every `/files` path in the merged app's route table must come from our
    own app, not get silently shadowed by (or shadow) one of aegra's own
    reserved routes (`/assistants`, `/threads`, `/runs`, `/store`,
    `/health`/`/ready`/`/live`/`/info`, `/docs`/`/redoc`/`/openapi.json`).
    """
    reserved = {"/assistants", "/threads", "/runs", "/store", "/health", "/ready", "/live", "/info"}
    all_routes = _flattened_routes(merged_app.routes)
    paths = {route.path for route in all_routes}

    assert paths & reserved, "sanity check: aegra's own reserved routes are actually present"
    files_paths = {route.path for route in all_routes if route.path.startswith("/files")}
    assert files_paths == {"/files", "/files/{key}"}, "an aegra-owned route now also lives under /files"


def test_the_files_app_contributes_no_middleware_of_its_own_once_mounted(merged_app):
    """Once mounted, `merged_app` is no longer middleware-free — `aegra` itself
    adds CORS/logging/correlation-id middleware to whatever app it merges
    with. So the guarantee to re-check here isn't "zero middleware" (that
    would be wrong once mounted) — it's that every entry still traces back to
    `aegra`'s own known stack, not something the files app added on top of it.
    """
    aegra_middleware = {
        ("aegra_api.middleware.content_type_fix", "ContentTypeFixMiddleware"),
        ("starlette.middleware.cors", "CORSMiddleware"),
        ("asgi_correlation_id.middleware", "CorrelationIdMiddleware"),
        ("aegra_api.middleware.logger_middleware", "StructLogMiddleware"),
    }
    actual = {(m.cls.__module__, m.cls.__name__) for m in merged_app.user_middleware}
    assert actual <= aegra_middleware, actual - aegra_middleware
