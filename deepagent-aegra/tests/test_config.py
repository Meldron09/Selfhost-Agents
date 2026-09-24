"""`OLLAMA_MODEL`/`OLLAMA_CONTEXT_WINDOW` are required, fail-loud config —
Ollama's own real default (`num_ctx=2048`) would silently misconfigure
summarization for a large-context model, so there is no baked-in fallback.
"""
from __future__ import annotations

import pytest

from agent.config import get_settings


def test_missing_ollama_model_fails_loudly(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", "32768")
    with pytest.raises(RuntimeError, match="OLLAMA_MODEL"):
        get_settings()


def test_missing_ollama_context_window_fails_loudly(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OLLAMA_MODEL", "gpt-oss:20b")
    monkeypatch.delenv("OLLAMA_CONTEXT_WINDOW", raising=False)
    with pytest.raises(RuntimeError, match="OLLAMA_CONTEXT_WINDOW"):
        get_settings()


def test_blank_ollama_model_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OLLAMA_MODEL", "   ")
    monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", "32768")
    with pytest.raises(RuntimeError, match="OLLAMA_MODEL"):
        get_settings()


def test_non_integer_context_window_fails_loudly(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OLLAMA_MODEL", "gpt-oss:20b")
    monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", "not-a-number")
    with pytest.raises(RuntimeError, match="OLLAMA_CONTEXT_WINDOW"):
        get_settings()


def test_ollama_base_url_defaults_to_localhost(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OLLAMA_MODEL", "gpt-oss:20b")
    monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", "32768")
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    settings = get_settings()
    assert settings.ollama_base_url == "http://localhost:11434"


def test_file_store_dir_defaults_and_is_absolute(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OLLAMA_MODEL", "gpt-oss:20b")
    monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", "32768")
    monkeypatch.delenv("FILE_STORE_DIR", raising=False)
    settings = get_settings()
    assert settings.file_store_dir.is_absolute()
    assert settings.file_store_dir.name == "data"
