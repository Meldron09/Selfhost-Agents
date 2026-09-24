"""The checkpointer seam: selection, fallback, and the wiring the standalone
entry points need.

A real Postgres round-trip is `scripts/verify_stack.py`; these tests are about
the parts that must be right *before* a database is reachable — and about
`agent/checkpointer.py`'s continued relevance, which is easy to lose track of
now that the served graph (`aegra`, like `langgraph dev` before it) overwrites
whatever checkpointer it carries with its own Postgres-backed one. This module
carries the durable-state wiring for the entry points that own their own event
loop instead: `agent.runner` and `scripts/verify_stack.py`.
"""
from __future__ import annotations

from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from agent.checkpointer import database_url, state_target, sync_checkpointer

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_no_database_url_means_no_durable_state(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert database_url() is None
    assert state_target() == "in-memory"


def test_blank_database_url_is_treated_as_unset(monkeypatch):
    """A commented-out value that became `DATABASE_URL=` must not be a conninfo."""
    monkeypatch.setenv("DATABASE_URL", "   ")
    assert database_url() is None


def test_a_run_without_a_database_still_gets_a_checkpointer(monkeypatch):
    """An unattended run must not fail for want of Postgres."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with sync_checkpointer() as saver:
        assert isinstance(saver, InMemorySaver)


def test_state_target_never_prints_the_password(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://admin:hunter2@db.example.com:5432/agent")
    target = state_target()
    assert "hunter2" not in target
    assert "admin" not in target
    assert target == "db.example.com:5432/agent"


def test_runner_and_verify_stack_get_their_checkpointer_from_sync_checkpointer():
    """The two standalone entry points own their own event loop, so they take
    `sync_checkpointer()` directly rather than through a server config hook —
    there is no `aegra.json` equivalent of `langgraph.json`'s `checkpointer.path`
    (see `tests/test_aegra_host_config.py::test_the_template_carries_no_langgraph_json_only_keys`).
    A regression here (one of them stops sourcing its checkpointer from this
    module) would silently fall back to in-memory state with nothing failing
    loudly, exactly the failure mode the old config-file test guarded against.
    """
    import agent.runner as runner

    assert runner.sync_checkpointer is sync_checkpointer

    verify_stack_source = (REPO_ROOT / "scripts" / "verify_stack.py").read_text()
    assert "from agent.checkpointer import" in verify_stack_source
    assert "sync_checkpointer" in verify_stack_source


def test_make_checkpointer_is_an_async_context_manager():
    """The shape `langgraph_api._checkpointer` accepts: a callable yielding a saver."""
    import inspect

    from agent.checkpointer import make_checkpointer

    assert callable(make_checkpointer)
    manager = make_checkpointer()
    assert hasattr(manager, "__aenter__")
    assert inspect.isasyncgenfunction(make_checkpointer.__wrapped__)
