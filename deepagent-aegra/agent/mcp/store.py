"""The Connection Store: every Connection's credentials and enabled state.

One whole-file Fernet-encrypted JSON document, `connections.enc`, in
`MCP_STATE_DIR` (see docs/adr/0008-mcp-connections-persist-across-runs.md):

    {"version": 1, "connections": {"github": {
        "credentials": {<Registry Entry field name>: value},
        "enabled": bool, "login": str, "scopes": [str],
        "toolCount": int, "lastError": str | None}}}

The key comes from `MCP_STORE_KEY`, read lazily on every touch — never at
import — so the stack boots for people who never use MCP. A wrong key or a
corrupt file is a loud `StoreError`; the file is never silently reset.

Synchronous file I/O: async callers should go through `asyncio.to_thread`.
"""
from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from agent.config import mcp_state_dir

_VERSION = 1
_FILENAME = "connections.enc"

# ponytail: in-process lock only — several server processes sharing one
# volume would need a file lock (fcntl.flock) around read-modify-write.
_LOCK = threading.Lock()


class StoreError(RuntimeError):
    """The store cannot be read or written safely."""


def _fernet() -> Fernet:
    key = (os.getenv("MCP_STORE_KEY") or "").strip()
    if not key:
        msg = (
            "MCP_STORE_KEY is not set. Generate one with:\n"
            '  python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"\n'
            "and set it in .env."
        )
        raise StoreError(msg)
    try:
        return Fernet(key.encode())
    except ValueError as exc:
        msg = "MCP_STORE_KEY is not a valid Fernet key (32 url-safe base64-encoded bytes)."
        raise StoreError(msg) from exc


def _read(fernet: Fernet) -> dict[str, dict[str, Any]]:
    path = mcp_state_dir() / _FILENAME
    try:
        blob = path.read_bytes()
    except FileNotFoundError:
        return {}
    try:
        doc = json.loads(fernet.decrypt(blob))
    except (InvalidToken, ValueError) as exc:
        msg = (
            f"Cannot decrypt {path}: wrong MCP_STORE_KEY or a corrupt file. "
            "Refusing to reset it — restore the key or delete the file deliberately."
        )
        raise StoreError(msg) from exc
    if not isinstance(doc, dict) or doc.get("version") != _VERSION:
        msg = f"{path} has an unsupported schema version (expected {_VERSION})."
        raise StoreError(msg)
    if not isinstance(doc.get("connections"), dict):
        msg = f"{path} is malformed: no `connections` mapping."
        raise StoreError(msg)
    return doc["connections"]


def _write(fernet: Fernet, connections: dict[str, dict[str, Any]]) -> None:
    directory = mcp_state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"version": _VERSION, "connections": connections}).encode()
    tmp = directory / f"{_FILENAME}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(fernet.encrypt(payload))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, directory / _FILENAME)


def _modify(mutate: Callable[[dict[str, dict[str, Any]]], object]) -> None:
    """Read-modify-write under the lock; `mutate` edits the connections dict."""
    with _LOCK:
        fernet = _fernet()
        connections = _read(fernet)
        mutate(connections)
        _write(fernet, connections)


def load() -> dict[str, dict[str, Any]]:
    """Every Connection, keyed by server slug (empty on a fresh store)."""
    with _LOCK:
        return _read(_fernet())


def save_connection(
    name: str,
    credentials: dict[str, str],
    *,
    login: str,
    scopes: list[str],
    tool_count: int = 0,
    enabled: bool | None = True,
) -> bool:
    """Create or replace a Connection (a fresh save clears any `lastError`).

    `enabled=None` keeps an existing Connection's value (True if new), decided
    under the same lock as the write. Returns the `enabled` that was saved.
    """
    saved = True

    def mutate(connections: dict[str, dict[str, Any]]) -> None:
        nonlocal saved
        if enabled is None:
            saved = connections.get(name, {}).get("enabled", True)
        else:
            saved = enabled
        connections[name] = {
            "credentials": dict(credentials),
            "enabled": saved,
            "login": login,
            "scopes": list(scopes),
            "toolCount": tool_count,
            "lastError": None,
        }

    _modify(mutate)
    return saved


def _update(name: str, **fields: Any) -> None:
    def mutate(connections: dict[str, dict[str, Any]]) -> None:
        connections[name].update(fields)  # KeyError if the Connection is unknown

    _modify(mutate)


def set_enabled(name: str, enabled: bool) -> None:
    _update(name, enabled=enabled)


def set_last_error(name: str, error: str | None) -> None:
    _update(name, lastError=error)


def delete(name: str) -> None:
    """Remove a Connection; a no-op if it isn't there."""
    _modify(lambda connections: connections.pop(name, None))
