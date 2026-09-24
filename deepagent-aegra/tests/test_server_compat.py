"""ASGI-hosted-graph compatibility: nothing may block the event loop.

The graph is served inside an ASGI event loop (`aegra serve`) — a
synchronous filesystem or subprocess call made *on that loop* degrades or
rejects every concurrent run. Ported near-verbatim from
`agent-runtime/tests/test_server_compat.py`
(docs/adr/0005-testing-strategy-carryover-from-agent-runtime.md, point 3):
same architecture, same concern, unaffected by anything this project
dropped (no workspace, no skills, no sandbox — this graph's default backend
is in-memory, so there is even less here that could touch disk).

This is easy to reintroduce by accident — `Path.resolve()` alone is enough,
because it calls `os.getcwd()` — and the failure only appears once the graph
is served, not in a normal test run. So these tests reproduce the constraint
directly, using `blockbuster`.

`_server_like_blockbuster` mirrors `langgraph_runtime_inmem.queue._enable_blockbuster`
— the checks the real server actually enforces — the same relaxation
`agent-runtime`'s own version of this file uses, and for the same reason:
`create_deep_agent` itself (not this project's own code) does blocking
`os.stat`/`os.path.*` work during construction — verified directly:
`deepagents.profiles._builtin_profiles._invoke_profile_plugins` enumerates
`importlib.metadata` entry points to discover third-party provider-profile
plugins, and `importlib.metadata`'s distribution lookup calls `os.stat`.
This graph never mounts a real `FilesystemBackend` (its default backend is
`StateBackend()`, per `deepagents/graph.py`) — that particular *agent-runtime*
trigger doesn't apply here — but the entry-point scan fires on every
`create_deep_agent` call regardless of backend, so the same relaxation is
still needed. Testing against a *stricter* config than production would just
fail on upstream code, not on anything this project's own code did wrong.
"""
from __future__ import annotations

import asyncio

import pytest
from blockbuster import BlockBuster
from langchain_core.messages import AIMessage, HumanMessage

from agent.graph import build_agent
from agent.scripted_model import ScriptedChatModel

_DISABLED_CHECKS = (
    "os.stat",
    "os.listdir",
    "os.remove",
    "io.BufferedReader.read",
    "io.BufferedWriter.write",
    "io.TextIOWrapper.read",
    "io.TextIOWrapper.write",
    "threading.Lock.acquire",
)


def _server_like_blockbuster() -> BlockBuster:
    bb = BlockBuster(excluded_modules=[])
    disable = [*_DISABLED_CHECKS, *(f for f in bb.functions if f.startswith("os.path."))]
    for name in disable:
        function = bb.functions.pop(name, None)
        if function:
            function.deactivate()
    return bb


def _run_guarded(coro_factory):
    """Run a coroutine on a loop guarded exactly as the served graph's is."""

    async def main():
        bb = _server_like_blockbuster()
        bb.activate()
        try:
            return await coro_factory()
        finally:
            bb.deactivate()

    return asyncio.run(main())


def test_graph_construction_makes_no_blocking_calls():
    """`make_agent` runs on the event loop — it must be syscall-free."""

    async def build():
        return build_agent(model=ScriptedChatModel(final_text="hi"))

    agent = _run_guarded(build)
    assert agent is not None


def test_a_tool_using_run_makes_no_blocking_calls_from_our_code():
    """A full run — plan, write a file — stays loop-safe.

    The default backend for a graph with no `backend=` argument is
    in-memory (deepagents' `StateBackend`), so this mostly proves there is
    no *accidental* disk touch anywhere in this project's own code
    (`agent/config.py`, `agent/model.py`, `agent/graph.py`) — not that the
    file tools themselves are safe, which is deepagents' own concern.
    """

    def responder(messages, tools):
        seen = [
            call["name"]
            for m in messages
            if isinstance(m, AIMessage)
            for call in (m.tool_calls or [])
        ]
        if "write_file" not in seen:
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "write_file",
                    "args": {"file_path": "/hello.txt", "content": "hi\n"},
                    "id": "c1",
                }],
            )
        return AIMessage(content="Wrote it.")

    agent = build_agent(model=ScriptedChatModel(responder=responder))

    async def run():
        return await agent.ainvoke(
            {"messages": [HumanMessage(content="make a file")]},
            config={"configurable": {"thread_id": "t-block"}, "recursion_limit": 20},
        )

    result = _run_guarded(run)

    tool_outputs = [m.content for m in result["messages"] if getattr(m, "type", None) == "tool"]
    assert any("hello.txt" in str(out) for out in tool_outputs), tool_outputs


def test_settings_do_not_call_getcwd_per_read(monkeypatch: pytest.MonkeyPatch):
    """Regression: `Path.resolve()` in settings broke every served run."""
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", "32768")
    from agent.config import get_settings

    async def read_settings():
        return get_settings()

    settings = _run_guarded(read_settings)
    assert settings.file_store_dir.is_absolute()


def test_relative_file_store_dir_is_absolutised_without_disk_access(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", "32768")
    monkeypatch.setenv("FILE_STORE_DIR", "./relative-dir")
    from agent.config import get_settings

    settings = get_settings()
    assert settings.file_store_dir.is_absolute()
    assert "relative-dir" in str(settings.file_store_dir)
