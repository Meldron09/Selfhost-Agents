"""`agent.files.store`: the filesystem primitive behind the upload/download app.

Exercised directly (no HTTP layer) against a real on-disk fixture — the
routes-level tests in test_files_app.py cover the HTTP contract on top of
this.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent.files.store import load, resolve_attachment_bytes, save, store_output_bytes


def test_save_returns_a_uuid4_key_carrying_the_original_extension(tmp_path: Path):
    key = save(tmp_path, "report.xlsx", b"hello")

    assert key.endswith(".xlsx")
    stem = key[: -len(".xlsx")]
    assert len(stem) == 36  # a uuid4's canonical string length


def test_save_with_no_extension_yields_a_bare_uuid_key(tmp_path: Path):
    key = save(tmp_path, "README", b"hello")

    assert "." not in key


def test_save_writes_the_exact_bytes_under_the_returned_key(tmp_path: Path):
    key = save(tmp_path, "data.bin", b"\x00\x01\xff")

    assert (tmp_path / key).read_bytes() == b"\x00\x01\xff"


def test_save_creates_the_root_directory_if_missing(tmp_path: Path):
    root = tmp_path / "not-yet-created"

    key = save(root, "a.txt", b"hi")

    assert (root / key).is_file()


def test_two_uploads_of_the_same_filename_get_different_keys(tmp_path: Path):
    first = save(tmp_path, "same.txt", b"one")
    second = save(tmp_path, "same.txt", b"two")

    assert first != second
    assert (tmp_path / first).read_bytes() == b"one"
    assert (tmp_path / second).read_bytes() == b"two"


def test_load_round_trips_the_exact_bytes(tmp_path: Path):
    key = save(tmp_path, "report.txt", b"hello world")

    assert load(tmp_path, key) == b"hello world"


def test_load_of_an_unknown_key_raises_key_error(tmp_path: Path):
    with pytest.raises(KeyError):
        load(tmp_path, "does-not-exist.txt")


def test_load_refuses_to_escape_the_root(tmp_path: Path):
    root = tmp_path / "store"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("outside the store")

    with pytest.raises(KeyError):
        load(root, "../secret.txt")


def test_load_refuses_a_bare_dot_dot_key(tmp_path: Path):
    root = tmp_path / "store"
    root.mkdir()

    with pytest.raises(KeyError):
        load(root, "..")


def test_store_output_bytes_writes_under_the_configured_file_store_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("OLLAMA_MODEL", "gpt-oss:20b")
    monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", "32768")
    monkeypatch.setenv("FILE_STORE_DIR", str(tmp_path))

    key = store_output_bytes("report.xlsx", b"workbook bytes")

    assert key.endswith(".xlsx")
    assert (tmp_path / key).read_bytes() == b"workbook bytes"


def test_resolve_attachment_bytes_reads_under_the_configured_file_store_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("OLLAMA_MODEL", "gpt-oss:20b")
    monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", "32768")
    monkeypatch.setenv("FILE_STORE_DIR", str(tmp_path))
    key = save(tmp_path, "attachment.pdf", b"pdf bytes")

    assert resolve_attachment_bytes(key) == b"pdf bytes"


def test_resolve_attachment_bytes_of_an_unknown_key_raises_key_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("OLLAMA_MODEL", "gpt-oss:20b")
    monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", "32768")
    monkeypatch.setenv("FILE_STORE_DIR", str(tmp_path))

    with pytest.raises(KeyError):
        resolve_attachment_bytes("does-not-exist.pdf")
