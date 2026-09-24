"""aegra-host/graph_adapter.py must not block the event loop it's awaited on.

`aegra` awaits this adapter's `graph()` coroutine directly on its own server
event loop (the same shape `agent/graph.py:make_agent` is awaited under via
LangGraph Server), so the same constraint `tests/test_server_compat.py`
guards there applies here: `graph_adapter.py`'s own file I/O, and any
synchronous factory it calls, must run off the loop.
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from blockbuster import BlockBuster

REPO_ROOT = Path(__file__).resolve().parent.parent
ADAPTER_PATH = REPO_ROOT / "aegra-host" / "graph_adapter.py"


def _load_adapter() -> ModuleType:
    spec = importlib.util.spec_from_file_location("aegra_host_graph_adapter", ADAPTER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_sync_target(tmp_path: Path) -> Path:
    target = tmp_path / "graph.py"
    target.write_text("def build():\n    return 'a-compiled-graph'\n")
    return target


@pytest.fixture
def fake_async_target(tmp_path: Path) -> Path:
    target = tmp_path / "graph.py"
    target.write_text("async def build():\n    return 'a-compiled-graph'\n")
    return target


def _run_guarded(coro_factory):
    async def main():
        bb = BlockBuster(excluded_modules=[])
        bb.activate()
        try:
            return await coro_factory()
        finally:
            bb.deactivate()

    return asyncio.run(main())


def test_loading_a_sync_factory_makes_no_blocking_calls_on_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, fake_sync_target: Path
):
    monkeypatch.setenv("AEGRA_GRAPH_DEPENDENCY_PATH", str(fake_sync_target.parent))
    monkeypatch.setenv("AEGRA_GRAPH_TARGET", f"{fake_sync_target.name}:build")
    adapter = _load_adapter()

    result = _run_guarded(adapter.graph)

    assert result == "a-compiled-graph"


def test_loading_an_async_factory_makes_no_blocking_calls_on_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, fake_async_target: Path
):
    monkeypatch.setenv("AEGRA_GRAPH_DEPENDENCY_PATH", str(fake_async_target.parent))
    monkeypatch.setenv("AEGRA_GRAPH_TARGET", f"{fake_async_target.name}:build")
    adapter = _load_adapter()

    result = _run_guarded(adapter.graph)

    assert result == "a-compiled-graph"


def test_a_missing_env_var_raises_before_any_import(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AEGRA_GRAPH_DEPENDENCY_PATH", raising=False)
    monkeypatch.delenv("AEGRA_GRAPH_TARGET", raising=False)
    adapter = _load_adapter()

    with pytest.raises(RuntimeError):
        asyncio.run(adapter.graph())
