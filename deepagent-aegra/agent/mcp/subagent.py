"""The `mcp` subagent: every enabled Connection's tools, connected per delegation.

A `CompiledSubAgent` whose runnable, on each `task` delegation, reads the
Connection Store, connects the enabled Connections, and runs a fresh inner
`create_agent` over their tools inside that one `async with` -- so the clients
are closed when the delegation ends, and a change made in Settings (or by
`set_enabled`) takes effect on the next delegation. Tool names are namespaced
by server slug (`github_…`).

Approval gate (ADR-0008): the inner agent carries a `HumanInTheLoopMiddleware`,
built after `list_tools`, gating every tool not declared read-only (fail-closed,
so unannotated tools are gated). Approve/reject only, per call, always on --
independent of `REQUIRE_APPROVAL`. On resume the runnable re-runs, re-reading the
store and rebuilding the gate from the fresh tool list, so a change in Settings
mid-approval drops the pending call (never auto-executes it): a disabled
Connection gets `NO_CONNECTIONS`, a removed one leaves an unknown tool name that
`ToolNode` refuses.

Known and accepted: the orchestrator's `ToolRetryMiddleware` wraps `task` by
name and cannot exclude `mcp` alone, so a transient inner failure after an
approved write restarts the inner agent (not a resume) and re-prompts. The
replayed write is re-gated, never silently duplicated. Revisit only if it shows
up in real use.

Construction rules (issue #27's HITL spike, branch `research/mcp-hitl-spike`): the runnable is async;
the ambient `config` is passed into `inner.ainvoke`; the inner agent has no
`checkpointer` of its own; its input is only `{"messages": ...}`.

Each Connection connects in its own try/except: a failure is recorded as its
`lastError` (re-probing GitHub to tell a rejected token from an unreachable
network) and the delegation carries on with those that connected. If none
connected, the failures are relayed as text.

Async only. `RunnableLambda(async_fn).invoke` raises `TypeError`, and so do
MCP tools, so the sync path (`agent.runner`, `agent.stream`) cannot delegate to
`mcp` -- documented, not shimmed; aegra serves runs async.
"""
from __future__ import annotations

import asyncio
import contextlib
from contextlib import AsyncExitStack
from typing import Any

from deepagents.middleware.subagents import CompiledSubAgent
from fastmcp import Client
from fastmcp.client.group import ClientGroup
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.mcp import MCPAdapter
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from agent.mcp import store
from agent.mcp.github import GitHubError, build_transport, probe_token, redact

# Both default to "no timeout" in fastmcp; a hung MCP server must not hang a run.
INIT_TIMEOUT = 30
REQUEST_TIMEOUT = 60

NO_CONNECTIONS = (
    "No MCP connections are enabled, so nothing was run. "
    "Any action that was awaiting approval has been dropped."
)

DESCRIPTION = (
    "Use the person's connected external services, e.g. GitHub repos, issues and PRs. "
    "Give it one self-contained request per call."
)

SYSTEM_PROMPT = (
    "You act on the person's connected external services using only the tools provided. "
    "Report what the tools actually returned; if a tool errors or is not found, say so "
    "plainly instead of guessing or retrying it blindly. A tool call reported as "
    "rejected, invalid or not found was not run and must not be retried."
)


def read_only(tool: Any) -> bool:
    """Whether the server declared `readOnlyHint: true`; anything else is gated.

    The hint sits at this exact path, snake_case, because `langchain.mcp`
    converts with `annotations.model_dump(exclude_none=True)`; a missing key or
    a null hint means no `annotations` entry at all. `is True` keeps it
    fail-closed. Pinned by `test_canary_read_only_hint_lives_at_the_metadata_path_the_gate_reads`.
    """
    annotations = ((tool.metadata or {}).get("mcp", {}).get("tool", {}).get("annotations")) or {}
    return annotations.get("read_only_hint") is True


def _say(text: str) -> dict[str, Any]:
    return {"messages": [AIMessage(text)]}


async def _record(name: str, error: str | None) -> None:
    # The Connection may have been deleted while this delegation ran.
    with contextlib.suppress(KeyError):
        await asyncio.to_thread(store.set_last_error, name, error)


async def _failure_reason(credentials: dict[str, str], exc: Exception) -> str:
    """Why a connect failed: re-run the token probe for a useful 401-vs-unreachable message."""
    try:
        await probe_token(credentials.get("Authorization", ""))
    except GitHubError as probe_exc:
        reason = str(probe_exc)
    else:
        reason = f"MCP connection failed: {type(exc).__name__}: {exc}"
    return redact(reason, credentials)  # persisted as lastError and shown to the person


async def _connect(stack: AsyncExitStack, name: str, credentials: dict[str, str]) -> list:
    """Connect one Connection (closed with `stack`) and list its tools, `name_`-prefixed."""
    client = Client(build_transport(credentials), init_timeout=INIT_TIMEOUT, timeout=REQUEST_TIMEOUT)
    # ponytail: one single-member ClientGroup per Connection, not one shared group --
    # ClientGroup connects all-or-nothing, and a bad Connection must not sink the rest.
    adapter = await stack.enter_async_context(MCPAdapter(ClientGroup({name: client})))
    return await adapter.list_tools()


def build_mcp_subagent(model: BaseChatModel) -> CompiledSubAgent:
    """The `mcp` spec; `model` drives the inner agent (the orchestrator's own)."""

    async def run(state: dict[str, Any], config: Any) -> dict[str, Any]:
        try:
            connections = await asyncio.to_thread(store.load)
        except store.StoreError as exc:
            return _say(f"MCP connections are unavailable: {exc}")
        enabled = {n: c for n, c in connections.items() if c.get("enabled")}
        if not enabled:
            return _say(NO_CONNECTIONS)

        async with AsyncExitStack() as stack:
            tools: list = []
            failures: dict[str, str] = {}
            for name, conn in enabled.items():
                try:
                    tools += await _connect(stack, name, conn["credentials"])
                except Exception as exc:  # noqa: BLE001 - any connect failure is per-Connection
                    failures[name] = await _failure_reason(conn["credentials"], exc)
                    await _record(name, failures[name])
                else:
                    if conn.get("lastError"):
                        await _record(name, None)

            if len(failures) == len(enabled):
                lines = "\n".join(f"- {n}: {why}" for n, why in failures.items())
                return _say(f"Could not connect to any enabled MCP connection:\n{lines}")

            gate = {t.name: {"allowed_decisions": ["approve", "reject"]} for t in tools if not read_only(t)}
            inner = create_agent(
                model,
                tools=tools,
                system_prompt=SYSTEM_PROMPT,
                middleware=[HumanInTheLoopMiddleware(interrupt_on=gate)],
                name="mcp-inner",
            )
            return await inner.ainvoke({"messages": state["messages"]}, config)

    return {"name": "mcp", "description": DESCRIPTION, "runnable": RunnableLambda(run)}
