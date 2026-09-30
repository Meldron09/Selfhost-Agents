"""The `/mcp/connections` settings API: `TestClient` against `create_app()`.

Real Connection Store (tmp_path, real Fernet); GitHub's `/user` endpoint is an
`httpx.MockTransport`; the GitHub MCP server is a local in-memory FastMCP
server (or a closed port for the unreachable case). Nothing leaves the machine.
Contract: the module docstring of agent/mcp/app.py.
"""
from __future__ import annotations

import logging

import httpx
import pytest
from cryptography.fernet import Fernet
from fastmcp import FastMCP
from fastmcp.client.transports import FastMCPTransport, StreamableHttpTransport
from starlette.testclient import TestClient

from agent.files import create_app
from agent.mcp import app as mcp_app
from agent.mcp import github, servers, store

TOKEN = "ghp_supersecrettoken1234567890"
BODY = {"Authorization": f"Bearer {TOKEN}"}
URL = "/mcp/connections/github/credentials"


def _fake_github_server() -> FastMCP:
    server = FastMCP("fake-github")

    @server.tool
    def list_issues() -> str:
        return "[]"

    @server.tool
    def create_issue() -> str:
        return "made"

    return server


@pytest.fixture
def github_user(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Programmable `GET /user` answer; the real `probe_token` runs over it."""
    real_probe = github.probe_token
    answer = {"status": 200, "json": {"login": "octocat"}, "headers": {"X-OAuth-Scopes": "repo, read:org"}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(answer["status"], json=answer["json"], headers=answer["headers"])

    async def probe(token: str):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await real_probe(token, client=client)

    monkeypatch.setattr(github, "probe_token", probe)
    return answer


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path, github_user) -> TestClient:
    monkeypatch.setenv("MCP_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_STORE_KEY", Fernet.generate_key().decode())
    server = _fake_github_server()
    monkeypatch.setattr(servers.SERVERS["github"], "build_transport", lambda credentials: FastMCPTransport(server))
    return TestClient(create_app())


def _connect(client: TestClient):
    return client.put(URL, json=BODY)


# --- GET /mcp/connections ---------------------------------------------------


def test_list_before_any_connection_has_the_registry_fields_and_no_connection(client):
    response = client.get("/mcp/connections")

    assert response.status_code == 200
    [entry] = response.json()
    assert entry["server"] == "github"
    assert entry["title"] and entry["description"]
    assert entry["connection"] is None
    [field] = entry["credentialFields"]
    assert field["name"] == "Authorization"
    assert field["isSecret"] is True
    assert field["isRequired"] is True
    assert field["description"]
    assert set(field) == {"name", "description", "isRequired", "isSecret"}


def test_list_after_connecting_shows_stored_state_and_never_the_secret(client):
    _connect(client)

    response = client.get("/mcp/connections")

    assert response.json()[0]["connection"] == {
        "enabled": True,
        "login": "octocat",
        "scopes": ["repo", "read:org"],
        "toolCount": 2,
        "lastError": None,
    }
    assert TOKEN not in response.text


def test_list_makes_no_network_calls(client, monkeypatch):
    _connect(client)

    def boom(*args, **kwargs):
        raise AssertionError("GET must read stored state only")

    monkeypatch.setattr(github, "probe_token", boom)
    monkeypatch.setattr(servers.SERVERS["github"], "build_transport", boom)

    assert client.get("/mcp/connections").status_code == 200


def test_list_surfaces_a_recorded_last_error(client):
    _connect(client)
    store.set_last_error("github", "token revoked")

    assert client.get("/mcp/connections").json()[0]["connection"]["lastError"] == "token revoked"


# --- PUT /mcp/connections/{server}/credentials ------------------------------


def test_first_connect_validates_persists_and_returns_the_success_details(client):
    response = _connect(client)

    assert response.status_code == 200
    assert response.json() == {
        "login": "octocat",
        "scopes": ["repo", "read:org"],
        "toolCount": 2,
        "enabled": True,
    }
    stored = store.load()["github"]
    assert stored["credentials"] == BODY
    assert stored["enabled"] is True


def test_a_rejected_token_is_422_with_the_inline_message_and_nothing_is_saved(client, github_user):
    github_user["status"] = 401

    response = _connect(client)

    assert response.status_code == 422
    assert response.json() == {"error": "GitHub rejected this token (401 Bad credentials)"}
    assert store.load() == {}


def test_github_unreachable_during_the_token_probe_is_502(client, github_user):
    github_user["status"] = 503

    response = _connect(client)

    assert response.status_code == 502
    assert response.json()["error"]
    assert store.load() == {}


def test_a_valid_token_but_unreachable_mcp_server_is_502_and_nothing_is_saved(client, monkeypatch):
    monkeypatch.setattr(
        servers.SERVERS["github"],
        "build_transport",
        lambda credentials: StreamableHttpTransport(
            "http://127.0.0.1:1/mcp/", headers={"Authorization": credentials["Authorization"]}
        ),
    )

    response = _connect(client)

    assert response.status_code == 502
    assert response.json()["error"].startswith(
        "Token is valid but the GitHub MCP server could not be reached: "
    )
    assert TOKEN not in response.text
    assert store.load() == {}


def test_an_mcp_error_that_quotes_the_token_is_redacted_from_the_502(client, monkeypatch):
    def leaky(credentials):
        raise RuntimeError(f"handshake failed for {credentials['Authorization']}")

    monkeypatch.setattr(servers.SERVERS["github"], "build_transport", leaky)

    response = _connect(client)

    assert response.status_code == 502
    assert TOKEN not in response.text
    assert "***" in response.json()["error"]


def test_the_real_github_transport_is_used_end_to_end_up_to_the_network(client, monkeypatch):
    """Unpatched `build_transport`, pointed at a closed port: a real streamable-http connect."""
    monkeypatch.setattr(servers.SERVERS["github"], "build_transport", github.build_transport)
    monkeypatch.setattr(github, "GITHUB_MCP_URL", "http://127.0.0.1:1/mcp/")

    response = _connect(client)

    assert response.status_code == 502
    assert response.json()["error"].startswith("Token is valid but the GitHub MCP server")


@pytest.mark.parametrize(
    "body",
    [{}, {"Authorization": ""}, {"Authorization": 123}, {"Authorization": None}, ["not", "an", "object"]],
)
def test_malformed_credentials_are_422_with_an_error_body(client, body):
    response = client.put(URL, json=body)

    assert response.status_code == 422
    assert list(response.json()) == ["error"]
    assert store.load() == {}


def test_a_non_json_body_is_422_and_does_not_echo_it(client):
    response = client.put(URL, content=f"{TOKEN} not json", headers={"Content-Type": "application/json"})

    assert response.status_code == 422
    assert list(response.json()) == ["error"]
    assert TOKEN not in response.text


def test_unknown_credential_fields_are_ignored_not_stored(client):
    client.put(URL, json={**BODY, "Extra": "x"})

    assert store.load()["github"]["credentials"] == BODY


def test_resubmit_replaces_credentials_preserves_enabled_and_clears_last_error(client):
    _connect(client)
    client.patch("/mcp/connections/github", json={"enabled": False})
    store.set_last_error("github", "token revoked")

    response = client.put(URL, json={"Authorization": "Bearer ghp_rotated"})

    assert response.json()["enabled"] is False
    stored = store.load()["github"]
    assert stored["credentials"] == {"Authorization": "Bearer ghp_rotated"}
    assert stored["enabled"] is False
    assert stored["lastError"] is None


def test_resubmit_over_an_enabled_connection_stays_enabled(client):
    _connect(client)

    assert client.put(URL, json={"Authorization": "Bearer ghp_rotated"}).json()["enabled"] is True


def test_a_store_write_failure_is_a_500_error_body(client, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store, "save_connection", boom)

    response = _connect(client)

    assert response.status_code == 500
    assert response.json() == {"error": "disk full"}


def test_a_failed_resubmit_leaves_the_existing_connection_untouched(client, github_user):
    _connect(client)
    store.set_last_error("github", "token revoked")
    github_user["status"] = 401

    client.put(URL, json={"Authorization": "Bearer ghp_bad"})

    stored = store.load()["github"]
    assert stored["credentials"] == BODY
    assert stored["lastError"] == "token revoked"


# --- PATCH /mcp/connections/{server} -----------------------------------------


def test_patch_toggles_enabled_and_nothing_else(client):
    _connect(client)

    assert client.patch("/mcp/connections/github", json={"enabled": False}).status_code == 200
    assert client.get("/mcp/connections").json()[0]["connection"]["enabled"] is False
    assert client.patch("/mcp/connections/github", json={"enabled": True}).status_code == 200
    connection = client.get("/mcp/connections").json()[0]["connection"]
    assert connection["enabled"] is True
    assert connection["login"] == "octocat"


def test_patch_without_a_connection_is_404(client):
    response = client.patch("/mcp/connections/github", json={"enabled": True})

    assert response.status_code == 404
    assert list(response.json()) == ["error"]


@pytest.mark.parametrize("body", [{}, {"enabled": "maybe"}, {"enabled": None}])
def test_patch_with_a_malformed_body_is_422(client, body):
    _connect(client)

    response = client.patch("/mcp/connections/github", json=body)

    assert response.status_code == 422
    assert list(response.json()) == ["error"]


# --- DELETE /mcp/connections/{server} ------------------------------------------


def test_delete_removes_credentials_and_state(client):
    _connect(client)

    response = client.delete("/mcp/connections/github")

    assert response.status_code == 204
    assert response.content == b""
    assert store.load() == {}
    assert client.get("/mcp/connections").json()[0]["connection"] is None


def test_delete_is_idempotent(client):
    assert client.delete("/mcp/connections/github").status_code == 204
    assert client.delete("/mcp/connections/github").status_code == 204


# --- errors, secrets ---------------------------------------------------------


@pytest.mark.parametrize(
    "method, path, kwargs",
    [
        ("put", "/mcp/connections/nope/credentials", {"json": BODY}),
        ("patch", "/mcp/connections/nope", {"json": {"enabled": True}}),
        ("delete", "/mcp/connections/nope", {}),
    ],
)
def test_an_unknown_server_is_404(client, method, path, kwargs):
    response = getattr(client, method)(path, **kwargs)

    assert response.status_code == 404
    assert list(response.json()) == ["error"]


def test_a_store_failure_is_a_500_with_the_message(client, monkeypatch):
    monkeypatch.delenv("MCP_STORE_KEY")

    response = client.get("/mcp/connections")

    assert response.status_code == 500
    assert "MCP_STORE_KEY" in response.json()["error"]


def test_no_secret_appears_in_any_response_or_log_line(client, caplog, github_user):
    caplog.set_level(logging.DEBUG)
    seen = [_connect(client).text, client.get("/mcp/connections").text]
    seen.append(client.patch("/mcp/connections/github", json={"enabled": False}).text)
    github_user["status"] = 401
    seen.append(client.put(URL, json=BODY).text)
    seen.append(client.put(URL, json={"Authorization": 5}).text)

    assert TOKEN not in "".join(seen)
    assert TOKEN not in caplog.text


def test_a_connection_without_login_or_scopes_is_listed_without_them(client):
    store.save_connection("github", BODY, tool_count=2)

    connection = client.get("/mcp/connections").json()[0]["connection"]

    assert connection == {"enabled": True, "toolCount": 2, "lastError": None}
