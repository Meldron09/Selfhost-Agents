"""Minimal working construction of the `mcp` CompiledSubAgent with a HITL gate (spike, not product code).

Import side effects: puts `deepagent-aegra/` on sys.path so the real `agent.*` modules
(`ScriptedChatModel`, `build_agent`) are reused unmodified.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "deepagent-aegra"))
sys.path.insert(0, str(Path(__file__).parent))
warnings.filterwarnings("ignore")

from fastmcp import Client  # noqa: E402
from fastmcp.client.group import ClientGroup  # noqa: E402
from langchain.agents import create_agent  # noqa: E402
from langchain.agents.middleware import HumanInTheLoopMiddleware  # noqa: E402
from langchain.mcp import MCPAdapter  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402
from langchain_core.runnables import RunnableLambda  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

NO_CONNECTIONS = (
    "No MCP connections are enabled, so nothing was run. "
    "Any action that was awaiting approval has been dropped."
)


INNER_PROMPT = (
    "You act on the person's connected services with the tools provided. Some tools need the person's "
    "approval; if a call is rejected, or a tool is reported as not valid or not found, it was NOT run "
    "(the connection may have changed while waiting): say so plainly and do not retry it."
)


class FakeStore:
    """Stand-in for the Connection Store: `{name: {url, token, enabled}}`; counts reads."""

    def __init__(self, conns: dict[str, dict]):
        self.conns = conns
        self.reads = 0

    def enabled(self) -> dict[str, dict]:
        self.reads += 1
        return {k: v for k, v in self.conns.items() if v["enabled"]}


def read_only(tool) -> bool:
    """Fail-closed: only an explicit `readOnlyHint: true` skips the gate."""
    ann = ((tool.metadata or {}).get("mcp", {}).get("tool", {}) or {}).get("annotations", {})
    return ann.get("read_only_hint") is True


def build_mcp_subagent(inner_model, store: FakeStore, *, inner_checkpointer=None) -> dict:
    async def run(state, config):
        conns = store.enabled()
        if not conns:
            return {"messages": [AIMessage(NO_CONNECTIONS)]}
        group = ClientGroup(
            {n: Client(c["url"], auth=c["token"], init_timeout=30, timeout=60) for n, c in conns.items()}
        )
        async with MCPAdapter(group) as adapter:
            tools = await adapter.list_tools()
            gate = {t.name: {"allowed_decisions": ["approve", "reject"]} for t in tools if not read_only(t)}
            inner = create_agent(
                inner_model,
                tools=tools,
                middleware=[HumanInTheLoopMiddleware(interrupt_on=gate)],
                checkpointer=inner_checkpointer() if callable(inner_checkpointer) else inner_checkpointer,  # None = inherit parent's via config
                system_prompt=INNER_PROMPT,
                name="mcp-inner",
            )
            # Pass the *ambient* config through: it carries the parent's checkpointer, thread_id and
            # checkpoint_ns, which is what lets interrupt() resume this fresh inner agent.
            return await inner.ainvoke({"messages": state["messages"]}, config)

    return {
        "name": "mcp",
        "description": "Use the person's connected external services (GitHub repos, issues, PRs).",
        "runnable": RunnableLambda(run),
    }


def build_graph(outer_model, inner_model, store, *, inner_checkpointer=None, saver=None, tool_retries=2):
    """The real `agent.graph.build_agent` stack (all real middleware), with `mcp` appended to the roster."""
    from unittest import mock

    import agent.graph as g
    from agent.config import Settings
    from agent.subagents import build_subagents

    mcp = build_mcp_subagent(inner_model, store, inner_checkpointer=inner_checkpointer)
    with mock.patch.object(g, "build_subagents", lambda: [*build_subagents(), mcp]):
        return g.build_agent(
            model=outer_model,
            settings=Settings(ollama_model="test-model", ollama_context_window=8192, tool_retries=tool_retries),
            checkpointer=saver or InMemorySaver(),
        )


def tool_names(messages) -> list[str]:
    return [c["name"] for m in messages if isinstance(m, AIMessage) for c in (m.tool_calls or [])]


def outer_responder(log: list):
    def r(messages, tools):
        log.append(len(messages))
        if "task" not in tool_names(messages):
            return AIMessage(
                content="",
                tool_calls=[{"name": "task", "args": {"description": "write x", "subagent_type": "mcp"}, "id": "o1"}],
            )
        return AIMessage(content="outer done: " + str(messages[-1].content))

    return r


def inner_responder(log: list, calls=("gh_write_thing",)):
    """Inner agent: one turn of (parallel) tool calls, then a final text reply."""

    def r(messages, tools):
        log.append({"n_messages": len(messages), "bound": list(tools)})
        if isinstance(messages[-1], HumanMessage):
            return AIMessage(
                content="",
                tool_calls=[{"name": n, "args": {"name": "x"}, "id": f"i{k}"} for k, n in enumerate(calls)],
            )
        tail = [str(m.content) for m in messages if getattr(m, "type", "") == "tool"]
        return AIMessage(content="inner done: " + " | ".join(tail))

    return r
