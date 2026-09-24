"""Runtime configuration, resolved once from the environment.

Ollama-only: there is no `MODEL_PROVIDER` dispatch var here the way
`agent-runtime` has one (see CONTEXT.md and docs/adr/ for why this project's
model plane is settled, not pluggable). Every knob still lives here so the
graph itself stays declarative.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_CWD = Path.cwd()
"""Captured once, at import.

`Path.resolve()` calls `os.getcwd()`, a blocking syscall — and settings are
read inside the graph factory, which aegra invokes *on the event loop*.
Resolving against this constant keeps path handling pure string work, so
graph construction makes no syscalls at all. See tests/test_server_compat.py.
"""


def _abs_path(name: str, default: str) -> Path:
    """Absolute, normalised path from the environment — without touching disk."""
    raw = os.getenv(name) or default
    path = Path(raw)
    if not path.is_absolute():
        path = _CWD / path
    return Path(os.path.normpath(path))


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value or not value.strip():
        msg = f"{name} is not set. Required — there is no baked-in default."
        raise RuntimeError(msg)
    return value.strip()


def _require_int(name: str) -> int:
    raw = _require(name)
    try:
        return int(raw)
    except ValueError as exc:
        msg = f"{name} must be an integer, got {raw!r}."
        raise RuntimeError(msg) from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    """Resolved runtime settings."""

    # --- model plane -----------------------------------------------------
    # Both fail loud, on purpose: Ollama's own real default (`num_ctx=2048`)
    # would silently misconfigure the model for whatever it's actually
    # running, and there is no sane guess for OLLAMA_MODEL at all.
    ollama_model: str
    ollama_context_window: int
    ollama_base_url: str = "http://localhost:11434"

    # --- file store (docs/adr/0002-dedicated-volume-for-file-store.md) ---
    # The dedicated disk directory the upload/download HTTP app (a later
    # ticket) will read and write through. No content lives here yet —
    # this ticket only carries the path and scripts/verify_stack.py's
    # round-trip check of it.
    file_store_dir: Path = field(default_factory=lambda: Path("./data"))

    # --- human in the loop ------------------------------------------------
    # Carried over from agent-runtime with an empty gate set: no tool in this
    # design is approval-gated, so this flag is inert but present — see
    # docs/adr/0005-testing-strategy-carryover-from-agent-runtime.md, point 5.
    require_approval: bool = False

    # --- planning / delegation --------------------------------------------
    # Ported from agent-runtime/agent/config.py: deepagents does not install
    # planning behaviour itself, so without this flag there is no way to
    # turn `write_todos` off (see agent/graph.py's `_build_middleware`).
    enable_todos: bool = True

    # --- long-run behaviour -------------------------------------------------
    # Ported from agent-runtime/agent/config.py: backstops for a run whose
    # tool calls fail transiently or that loops without ending (see
    # agent/graph.py's `_build_middleware`).
    tool_retries: int = 2
    model_call_limit: int = 400

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            ollama_model=_require("OLLAMA_MODEL"),
            ollama_context_window=_require_int("OLLAMA_CONTEXT_WINDOW"),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip(),
            file_store_dir=_abs_path("FILE_STORE_DIR", "./data"),
            require_approval=_env_bool("REQUIRE_APPROVAL", False),
            enable_todos=_env_bool("ENABLE_TODOS", True),
            tool_retries=_env_int("TOOL_RETRIES", 2),
            model_call_limit=_env_int("MODEL_CALL_LIMIT", 400),
        )


def get_settings() -> Settings:
    """Read settings from the current environment.

    Deliberately not cached: `aegra serve` loads `.env` after import, and
    tests monkeypatch the environment between cases.
    """
    return Settings.from_env()
