"""The upload/download HTTP app: isolated `TestClient` tests against the app
object directly — no live server, no live `aegra` process.

Route contract: docs/adr/0004-upload-download-http-app-route-contract.md.
Mirrors `agent-runtime/tests/test_artifacts.py`'s structural-guarantee style
for the analogous read-only app; test_aegra_host_http_mount.py re-verifies
the same guarantees once this app is actually mounted into `aegra`.
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import httpx
import pytest
from blockbuster import BlockBuster
from starlette.testclient import TestClient

from agent.files import app, create_app
from conftest import imported_modules

FILES_PKG = Path(__file__).resolve().parent.parent / "agent" / "files"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", "32768")
    monkeypatch.setenv("FILE_STORE_DIR", str(tmp_path))
    return TestClient(create_app())


# --- round trip ----------------------------------------------------------


def test_upload_then_download_round_trips_the_exact_bytes(client: TestClient):
    upload = client.post("/files", files={"file": ("report.xlsx", b"\x00binary\xff", "application/octet-stream")})

    assert upload.status_code == 200
    key = upload.json()["key"]
    assert key.endswith(".xlsx")

    download = client.get(f"/files/{key}")

    assert download.status_code == 200
    assert download.content == b"\x00binary\xff"


def test_upload_response_is_bare_key_with_no_other_fields(client: TestClient):
    upload = client.post("/files", files={"file": ("a.txt", b"hi", "text/plain")})

    assert upload.json().keys() == {"key"}


def test_download_sets_content_type_guessed_from_the_key_extension(client: TestClient):
    upload = client.post("/files", files={"file": ("a.txt", b"hi", "text/plain")})
    key = upload.json()["key"]

    download = client.get(f"/files/{key}")

    assert download.headers["content-type"].startswith("text/plain")


def test_download_sets_content_disposition_to_the_store_internal_key(client: TestClient):
    upload = client.post("/files", files={"file": ("original-name.txt", b"hi", "text/plain")})
    key = upload.json()["key"]

    download = client.get(f"/files/{key}")

    assert download.headers["content-disposition"] == f'attachment; filename="{key}"'
    assert "original-name.txt" not in download.headers["content-disposition"]


def test_download_of_a_missing_key_is_404(client: TestClient):
    response = client.get("/files/does-not-exist.txt")

    assert response.status_code == 404


def test_upload_accepts_any_bytes_with_no_extension_and_any_size(client: TestClient):
    upload = client.post("/files", files={"file": ("noext", b"x" * 10_000, "application/octet-stream")})

    assert upload.status_code == 200
    key = upload.json()["key"]
    assert "." not in key

    download = client.get(f"/files/{key}")
    assert download.content == b"x" * 10_000


# --- no listing endpoint ---------------------------------------------------


def test_there_is_no_listing_route():
    """`/files` (bare) exists only as the upload route — GET is never allowed on it."""
    for route in app.routes:
        if route.path == "/files":
            assert "GET" not in route.methods


# --- structural guarantees, enforced rather than described -----------------


def test_no_route_handler_is_async():
    for route in app.routes:
        assert not inspect.iscoroutinefunction(route.endpoint), route.path


def test_the_app_carries_no_global_middleware():
    assert app.user_middleware == []


def test_the_app_imports_nothing_from_the_graph_or_deepagents():
    for py_file in FILES_PKG.rglob("*.py"):
        for module in imported_modules(py_file):
            assert module != "agent.graph", f"{py_file} imports agent.graph"
            assert not module.startswith("agent.graph."), f"{py_file} imports agent.graph"
            assert module != "deepagents", f"{py_file} imports deepagents"
            assert not module.startswith("deepagents."), f"{py_file} imports deepagents"


def test_create_app_returns_a_fresh_instance_each_call():
    assert create_app() is not create_app()


# --- blockbuster: the loop-safety guard extends to these handlers ---------


async def _request_on_a_guarded_loop(asgi_app, method: str, url: str, **kwargs) -> httpx.Response:
    """Issue one request with every blockbuster check active on *this*
    coroutine's event loop — unlike test_server_compat.py's
    `_server_like_blockbuster`, which deliberately disables the `os.stat`/
    `io.Buffered*` checks to tolerate `deepagents`' own unavoidable calls
    during graph construction. This app has no such excuse: its handlers'
    file I/O must stay off the loop by running in Starlette's threadpool
    (guaranteed by being a plain `def`, not `async def`), not by a guard
    that's been told to look away from exactly the calls this app makes.
    """
    bb = BlockBuster(excluded_modules=[])
    bb.activate()
    try:
        transport = httpx.ASGITransport(app=asgi_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
            return await async_client.request(method, url, **kwargs)
    finally:
        bb.deactivate()


def test_upload_and_download_make_no_blocking_calls_on_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The ticket's own criterion: the blockbuster guard from
    tests/test_server_compat.py extends to this app's route handlers — a
    served request must never block `aegra`'s own event loop, unlike this
    test's earlier fixtures, which drive the client through a real thread
    pool but never actually run under a blockbuster-instrumented loop.
    """
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", "32768")
    monkeypatch.setenv("FILE_STORE_DIR", str(tmp_path))
    guarded_app = create_app()

    async def run() -> tuple[httpx.Response, httpx.Response]:
        upload = await _request_on_a_guarded_loop(
            guarded_app, "POST", "/files", files={"file": ("a.txt", b"hi", "text/plain")}
        )
        key = upload.json()["key"]
        download = await _request_on_a_guarded_loop(guarded_app, "GET", f"/files/{key}")
        return upload, download

    upload, download = asyncio.run(run())

    assert upload.status_code == 200
    assert download.status_code == 200
    assert download.content == b"hi"
