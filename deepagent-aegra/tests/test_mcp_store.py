"""The Connection Store: Fernet whole-file-encrypted `connections.enc`.

Real filesystem (tmp_path), real Fernet — nothing mocked. See
docs/adr/0008-mcp-connections-persist-across-runs.md.
"""
from __future__ import annotations

import importlib
import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.fernet import Fernet

from agent.mcp import store

TOKEN = "ghp_supersecrettoken1234567890"
CREDS = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def _store_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("MCP_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_STORE_KEY", Fernet.generate_key().decode())


def _file(tmp_path):
    return tmp_path / "connections.enc"


def test_load_on_a_fresh_store_is_empty():
    assert store.load() == {}


def test_save_connection_round_trips():
    store.save_connection(
        "github", CREDS, login="octocat", scopes=["repo", "read:org"], tool_count=12
    )
    assert store.load() == {
        "github": {
            "credentials": CREDS,
            "enabled": True,
            "login": "octocat",
            "scopes": ["repo", "read:org"],
            "toolCount": 12,
            "lastError": None,
        }
    }


def test_set_enabled_and_set_last_error_and_delete():
    store.save_connection("github", CREDS, login="octocat", scopes=[])
    store.set_enabled("github", False)
    assert store.load()["github"]["enabled"] is False

    store.set_last_error("github", "token rejected (401)")
    assert store.load()["github"]["lastError"] == "token rejected (401)"
    store.set_last_error("github", None)
    assert store.load()["github"]["lastError"] is None

    store.delete("github")
    assert store.load() == {}
    store.delete("github")  # idempotent


def test_saving_again_replaces_credentials_and_clears_last_error():
    store.save_connection("github", CREDS, login="octocat", scopes=[])
    store.set_last_error("github", "boom")
    store.save_connection("github", {"Authorization": "Bearer new"}, login="octocat", scopes=[])
    conn = store.load()["github"]
    assert conn["credentials"] == {"Authorization": "Bearer new"}
    assert conn["lastError"] is None


def test_updating_an_unknown_connection_raises():
    with pytest.raises(KeyError):
        store.set_enabled("github", True)
    with pytest.raises(KeyError):
        store.set_last_error("github", "x")


def test_no_plaintext_on_disk(tmp_path):
    store.save_connection("github", CREDS, login="octocat", scopes=["repo"])
    raw = _file(tmp_path).read_bytes()
    for needle in (TOKEN, "octocat", "credentials", "github"):
        assert needle.encode() not in raw
    assert not list(tmp_path.glob("*.tmp")), "atomic write left a temp file behind"


def test_wrong_key_is_a_loud_error_and_never_resets(tmp_path, monkeypatch):
    store.save_connection("github", CREDS, login="octocat", scopes=[])
    before = _file(tmp_path).read_bytes()

    monkeypatch.setenv("MCP_STORE_KEY", Fernet.generate_key().decode())
    with pytest.raises(store.StoreError):
        store.load()
    with pytest.raises(store.StoreError):
        store.save_connection("other", CREDS, login="x", scopes=[])
    assert _file(tmp_path).read_bytes() == before


def test_corrupt_file_is_a_loud_error_and_never_resets(tmp_path):
    _file(tmp_path).write_bytes(b"not a fernet token")
    with pytest.raises(store.StoreError):
        store.load()
    with pytest.raises(store.StoreError):
        store.delete("github")
    assert _file(tmp_path).read_bytes() == b"not a fernet token"


def test_valid_ciphertext_with_unknown_schema_version_is_an_error(tmp_path):
    key = os.environ["MCP_STORE_KEY"].encode()
    doc = json.dumps({"version": 2, "connections": {}}).encode()
    _file(tmp_path).write_bytes(Fernet(key).encrypt(doc))
    with pytest.raises(store.StoreError, match="version"):
        store.load()


def test_concurrent_writes_do_not_lose_updates():
    names = [f"server{i}" for i in range(25)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: store.save_connection(n, CREDS, login=n, scopes=[]), names))
    assert sorted(store.load()) == sorted(names)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: store.set_enabled(n, False), names))
    assert all(not c["enabled"] for c in store.load().values())


def test_missing_key_fails_loud_on_first_touch_not_at_import(monkeypatch):
    monkeypatch.delenv("MCP_STORE_KEY", raising=False)
    importlib.reload(store)  # import with no key must not raise
    with pytest.raises(store.StoreError, match="MCP_STORE_KEY") as exc:
        store.load()
    assert "Fernet.generate_key" in str(exc.value)
    with pytest.raises(store.StoreError, match="MCP_STORE_KEY"):
        store.save_connection("github", CREDS, login="x", scopes=[])


def test_malformed_key_is_a_loud_error(monkeypatch):
    monkeypatch.setenv("MCP_STORE_KEY", "not-a-fernet-key")
    with pytest.raises(store.StoreError, match="MCP_STORE_KEY"):
        store.load()


def test_valid_ciphertext_without_a_connections_mapping_is_a_store_error(tmp_path):
    key = os.environ["MCP_STORE_KEY"].encode()
    doc = json.dumps({"version": 1}).encode()
    _file(tmp_path).write_bytes(Fernet(key).encrypt(doc))
    with pytest.raises(store.StoreError):
        store.load()


def test_save_connection_with_enabled_none_preserves_the_existing_value():
    store.save_connection("github", CREDS, login="octocat", scopes=[], enabled=False)

    saved = store.save_connection("github", CREDS, login="octocat", scopes=[], enabled=None)

    assert saved is False
    assert store.load()["github"]["enabled"] is False


def test_save_connection_with_enabled_none_on_a_new_connection_is_enabled():
    assert store.save_connection("github", CREDS, login="octocat", scopes=[], enabled=None) is True


def test_login_and_scopes_are_optional_and_omitted_when_absent():
    store.save_connection("github", CREDS, tool_count=3)
    conn = store.load()["github"]
    assert "login" not in conn and "scopes" not in conn
    assert conn["toolCount"] == 3
