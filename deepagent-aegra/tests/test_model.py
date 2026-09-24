"""`get_model()` builds `ChatOllama` and attaches the synthetic `.profile`
shim `SummarizationMiddleware` (a later ticket) will read — see
docs/adr/0003-synthetic-profile-shim-for-ollama-summarization-threshold.md.
"""
from __future__ import annotations

import pytest
from langchain_ollama import ChatOllama

from agent.config import Settings
from agent.model import get_model


def test_get_model_without_settings_requires_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="OLLAMA_MODEL"):
        get_model()


def test_get_model_builds_chat_ollama():
    settings = Settings(ollama_model="gpt-oss:20b", ollama_context_window=32768)
    model = get_model(settings)
    assert isinstance(model, ChatOllama)
    assert model.model == "gpt-oss:20b"
    assert model.base_url == "http://localhost:11434"
    assert model.num_ctx == 32768


def test_get_model_attaches_the_profile_shim():
    """`langchain_ollama.ChatOllama` has no native `.profile` — this is the
    only thing that lets `SummarizationMiddleware`'s fraction-based trigger
    apply to an Ollama-served run instead of falling back to an absolute
    token count with no relation to what was actually configured.
    """
    settings = Settings(ollama_model="gpt-oss:20b", ollama_context_window=8192)
    model = get_model(settings)
    assert model.profile == {"max_input_tokens": 8192}
