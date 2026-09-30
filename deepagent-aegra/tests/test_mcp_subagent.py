"""The `mcp` subagent's connect path, through the real `build_agent` graph.

Real Connection Store (tmp_path, real Fernet), a real local MCP server, a
`ScriptedChatModel` for both the orchestrator and the inner agent — nothing
mocked but the GitHub-specific edges: `build_transport` is pointed at the local
server and `probe_token` at a canned answer.
"""
from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet
from fastmcp.client.transports import StreamableHttpTransport
from langchain_core.messages import AIMessage, HumanMessage

import fake_mcp_server
from agent.config import Settings
from agent.graph import build_agent
from agent.mcp import store, subagent
from agent.mcp.github import GitHubUnreachable, TokenRejected
from agent.scripted_model import ScriptedChatModel

_SETTINGS = Settings(ollama_model="test-model", ollama_context_window=8192)
URL = fake_mcp_server.start()


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("MCP_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_STORE_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(
        subagent,
        "build_transport",
        lambda creds: StreamableHttpTransport(creds["url"], headers={"Authorization": creds["Authorization"]}),
    )
    fake_mcp_server.SEEN_AUTH.clear()


def _connect(name: str, url: str = URL, **kw) -> None:
    store.save_connection(name, {"Authorization": f"Bearer tok-{name}", "url": url}, login=name, scopes=[], **kw)


def _tool_names(messages) -> list[str]:
    return [c["name"] for m in messages if isinstance(m, AIMessage) for c in (m.tool_calls or [])]


def _outer(messages, tools):
    if "task" not in _tool_names(messages):
        args = {"description": "look something up", "subagent_type": "mcp"}
        return AIMessage(content="", tool_calls=[{"name": "task", "args": args, "id": "o1"}])
    return AIMessage(content="outer: " + str(messages[-1].content))


def _inner(calls: list[str], seen: list):
    """Inner agent: call each named tool once, then report the tool results."""

    def responder(messages, tools):
        seen.append(list(tools))
        if isinstance(messages[-1], HumanMessage):
            return AIMessage(
                content="",
                tool_calls=[{"name": n, "args": {"name": "x"}, "id": f"i{k}"} for k, n in enumerate(calls)],
            )
        return AIMessage(content="inner: " + " | ".join(str(m.content) for m in messages if m.type == "tool"))

    return responder


class _Run:
    def __init__(self, inner_calls=("github_read_thing",)):
        self.bound: list = []
        self.agent = build_agent(model=_Both(_outer, _inner(list(inner_calls), self.bound)), settings=_SETTINGS)

    def ask(self, thread: str = "t1") -> str:
        result = asyncio.run(
            self.agent.ainvoke({"messages": [HumanMessage("use github")]}, config={"configurable": {"thread_id": thread}})
        )
        return str(result["messages"][-1].content)


def _Both(outer, inner):
    """One scripted model for orchestrator and inner agent, as in production.

    The orchestrator's tools include `task`; the inner agent's never do.
    """

    def responder(messages, tools):
        return (outer if "task" in tools else inner)(messages, tools)

    return ScriptedChatModel(responder=responder)


def test_delegation_calls_a_read_tool_and_returns_its_result():
    _connect("github")
    run = _Run()
    reply = run.ask()

    assert "thing:x" in reply
    assert "github_read_thing" in run.bound[0] and "github_write_thing" in run.bound[0]
    assert fake_mcp_server.SEEN_AUTH and set(fake_mcp_server.SEEN_AUTH) == {"Bearer tok-github"}


def test_group_is_closed_after_each_delegation(monkeypatch):
    opened = []

    class SpyAdapter(subagent.MCPAdapter):
        def __init__(self, target):
            super().__init__(target)
            opened.append(self._client)  # the adapter's own (armed, cloned) group

    monkeypatch.setattr(subagent, "MCPAdapter", SpyAdapter)
    _connect("github")
    run = _Run()
    run.ask("t1")
    run.ask("t2")

    assert len(opened) == 2  # a fresh group per delegation
    assert [c.is_connected() for g in opened for c in g.clients.values()] == [False, False]


@pytest.mark.parametrize("setup", ["empty-store", "disabled"])
def test_zero_enabled_connections_gets_the_plain_reply(setup):
    if setup == "disabled":
        _connect("github", enabled=False)
    reply = _Run().ask()

    assert "No MCP connections are enabled" in reply
    assert "dropped" in reply
    assert fake_mcp_server.SEEN_AUTH == []


def test_disabling_in_the_store_takes_effect_on_the_next_delegation():
    _connect("github")
    run = _Run()
    assert "thing:x" in run.ask("t1")

    store.set_enabled("github", False)
    assert "No MCP connections are enabled" in run.ask("t2")


def test_one_failing_connection_still_serves_the_other_and_records_last_error(monkeypatch):
    async def probe(token, **_):
        raise GitHubUnreachable("could not reach api.github.com: boom")

    monkeypatch.setattr(subagent, "probe_token", probe)
    _connect("github")
    _connect("other", url=fake_mcp_server.dead_url())

    reply = _Run().ask()

    assert "thing:x" in reply
    conns = store.load()
    assert "boom" in conns["other"]["lastError"]
    assert conns["github"]["lastError"] is None


def test_a_rejected_token_records_the_401_reason(monkeypatch):
    async def probe(token, **_):
        raise TokenRejected("GitHub rejected the token (401)")

    monkeypatch.setattr(subagent, "probe_token", probe)
    _connect("github", url=fake_mcp_server.dead_url())

    reply = _Run().ask()

    assert "rejected the token" in reply  # all failed: the failures are relayed as text
    assert "rejected the token" in store.load()["github"]["lastError"]


def test_a_successful_connect_clears_last_error():
    _connect("github")
    store.set_last_error("github", "stale failure")

    _Run().ask()

    assert store.load()["github"]["lastError"] is None


def test_a_missing_store_key_is_relayed_not_raised(monkeypatch):
    monkeypatch.delenv("MCP_STORE_KEY")
    reply = _Run().ask()
    assert "MCP_STORE_KEY" in reply


def test_last_error_never_stores_the_token(monkeypatch):
    async def probe(token, **_):
        raise GitHubUnreachable("upstream said: Bearer tok-github was refused")

    monkeypatch.setattr(subagent, "probe_token", probe)
    _connect("github", url=fake_mcp_server.dead_url())

    reply = _Run().ask()

    assert "tok-github" not in store.load()["github"]["lastError"]
    assert "tok-github" not in reply
