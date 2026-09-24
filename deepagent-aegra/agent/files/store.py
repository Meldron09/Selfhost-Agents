"""Filesystem store backing the upload/download HTTP app.

Keys are opaque, server-generated `"<uuid4><ext>"` tokens
(docs/adr/0004-upload-download-http-app-route-contract.md) — this module is
the only thing that ever constructs or resolves one to a path on disk. A
caller passes a key straight through without inspecting it; it never
constructs `root / key` itself.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from agent.config import get_settings


def save(root: Path, filename: str, data: bytes) -> str:
    """Write `data` under `root` as a fresh key; returns that key."""
    key = f"{uuid.uuid4()}{Path(filename).suffix}"
    root.mkdir(parents=True, exist_ok=True)
    (root / key).write_bytes(data)
    return key


def load(root: Path, key: str) -> bytes:
    """Read the bytes stored under `key`; raises `KeyError` if it doesn't resolve.

    `key` is server-generated and never contains a `/` in the route that
    reaches this function (see `agent/files/app.py`), but a single segment
    like `".."` still resolves one directory up through plain `/` joining —
    the resolved-path check below is what actually keeps a lookup confined
    to `root`, not the absence of a slash. Duplicates the same shape as
    `agent-runtime`'s `FilesystemReadOnlyStore.get` rather than importing it
    (docs/adr/0005's carryover is a convention, not a runtime dependency
    between the two projects) — this is that duplication's own copy of the
    guard, kept for the same reason: `/files/{key}` is public HTTP, reachable
    by any client, not only the trusted callers ADR-0004 describes.
    """
    root = root.resolve()
    path = (root / key).resolve()
    if path != root and root not in path.parents:
        raise KeyError(key)
    if not path.is_file():
        raise KeyError(key)
    return path.read_bytes()


def store_output_bytes(filename: str, data: bytes) -> str:
    """`output-writer`'s write-side mirror of `resolve_attachment_bytes`.

    Reads settings at call time, not import time — same convention as every
    other settings access in this project (`aegra serve` loads `.env` after
    import; tests monkeypatch the environment between cases).
    """
    return save(get_settings().file_store_dir, filename, data)


def resolve_attachment_bytes(key: str) -> bytes:
    """`file-reader`'s read-side mirror of `store_output_bytes`.

    Raises `KeyError` if `key` doesn't resolve — `agent/file_reader.py`'s
    tools catch this and turn it into an explicit tool-level error, never
    letting it propagate as a raised exception into the model loop.
    """
    return load(get_settings().file_store_dir, key)
