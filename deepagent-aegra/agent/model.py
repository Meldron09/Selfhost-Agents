"""Model plane: Ollama, and only Ollama.

No `MODEL_PROVIDER` dispatch — this project's model plane is settled, not
pluggable (see CONTEXT.md). Tests never call `get_model()` at all: they pass
a `ScriptedChatModel` straight into `build_agent(model=...)`, which is what
keeps `pytest` credential-free and offline even though this module itself
requires `OLLAMA_MODEL`/`OLLAMA_CONTEXT_WINDOW` to be set.
"""
from __future__ import annotations

import os

from langchain_core.language_models.chat_models import BaseChatModel

from agent.config import Settings, get_settings


def get_model(settings: Settings | None = None) -> BaseChatModel:
    """Build the `ChatOllama` this project serves runs with.

    `num_ctx` is set explicitly from `OLLAMA_CONTEXT_WINDOW` and is not
    optional: Ollama defaults a request to a 4,096-token context and
    *silently truncates* anything longer, which would drop tool definitions
    off the front of the prompt with no visible error.

    `profile={"max_input_tokens": ...}` is a synthetic attribute
    `langchain_ollama.ChatOllama` does not otherwise expose (see
    docs/adr/0003-synthetic-profile-shim-for-ollama-summarization-threshold.md).
    A later ticket's `SummarizationMiddleware` reads it to trigger
    compaction at a fraction of the real context window instead of an
    absolute token count guessed with no relation to what was actually
    configured.
    """
    from langchain_ollama import ChatOllama

    settings = settings or get_settings()
    return ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=0,
        num_ctx=settings.ollama_context_window,
        reasoning=False,  # keep thinking out of `content`, which the UI renders
        client_kwargs={"timeout": int(os.getenv("MODEL_TIMEOUT", "600"))},
        profile={"max_input_tokens": settings.ollama_context_window},
    )
