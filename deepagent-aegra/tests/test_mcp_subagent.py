"""The `mcp` subagent's connect path and approval gate, through the real `build_agent` graph.

Real Connection Store (tmp_path, real Fernet), a real local MCP server, a
`ScriptedChatModel` for both the orchestrator and the inner agent — nothing
mocked but the GitHub-specific edges: `build_transport` is pointed at the local
server and `probe_token` at a canned answer.
"""
from __future__ import annotations

import asyncio

import pytest
from cryptography.fernet import Fernet
from fastmcp import Client
from fastmcp.client.group import ClientGroup
from fastmcp.client.transports import StreamableHttpTransport
from langchain.mcp import MCPAdapter
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

import fake_mcp_server
from agent.config import Settings
from agent.graph import build_agent
from agent.mcp import github, servers, store, subagent
from agent.mcp.github import GitHubUnreachable, TokenRejected
from agent.scripted_model import ScriptedChatModel

_SETTINGS = Settings(ollama_model="test-model", ollama_context_window=8192)
URL = fake_mcp_server.start()


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("MCP_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("MCP_STORE_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(
        servers.SERVERS["github"],
        "build_transport",
        lambda creds: StreamableHttpTransport(creds["url"], headers={"Authorization": creds["Authorization"]}),
    )
    # Tests use "other" as a second Connection slug; it behaves like GitHub.
    monkeypatch.setitem(servers.SERVERS, "other", servers.SERVERS["github"])
    fake_mcp_server.SEEN_AUTH.clear()
    fake_mcp_server.CALLS.clear()


def _connect(name: str, url: str = URL, **kw) -> None:
    store.save_connection(name, {"Authorization": f"Bearer tok-{name}", "url": url}, login=name, scopes=[], **kw)


def _tool_names(messages) -> list[str]:
    return [c["name"] for m in messages if isinstance(m, AIMessage) for c in (m.tool_calls or [])]


def _outer(messages, tools):
    if "task" not in _tool_names(messages):
        args = {"description": "look something up", "subagent_type": "mcp"}
        return AIMessage(content="", tool_calls=[{"name": "task", "args": args, "id": "o1"}])
    return AIMessage(content="outer: " + str(messages[-1].content))


def _inner(calls: list[str], seen: list, turns: list):
    """Inner agent: call each named tool once, then report the tool results.

    `turns` records how many messages each inner model call saw: a resume from
    the inner checkpoint shows `[2, 4]`, a restart would show a second `2`.
    """

    def responder(messages, tools):
        seen.append(list(tools))
        turns.append(len(messages))
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
        self.turns: list[int] = []
        self.agent = build_agent(
            model=_Both(_outer, _inner(list(inner_calls), self.bound, self.turns)),
            settings=_SETTINGS,
            checkpointer=InMemorySaver(),
        )

    def _config(self, thread: str) -> dict:
        return {"configurable": {"thread_id": thread}}

    def start(self, thread: str = "t1") -> dict:
        return asyncio.run(self.agent.ainvoke({"messages": [HumanMessage("use github")]}, config=self._config(thread)))

    def resume(self, decision: dict, thread: str = "t1") -> dict:
        return asyncio.run(self.agent.ainvoke(Command(resume=decision), config=self._config(thread)))

    def ask(self, thread: str = "t1") -> str:
        return _reply(self.start(thread))

    def pending(self, thread: str = "t1"):
        return asyncio.run(self.agent.aget_state(self._config(thread))).tasks


def _reply(result: dict) -> str:
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

    monkeypatch.setattr(github, "probe_token", probe)
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

    monkeypatch.setattr(github, "probe_token", probe)
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

    monkeypatch.setattr(github, "probe_token", probe)
    _connect("github", url=fake_mcp_server.dead_url())

    reply = _Run().ask()

    assert "tok-github" not in store.load()["github"]["lastError"]
    assert "tok-github" not in reply


# --- approval gate: every tool not declared read-only waits for a human -------

APPROVE = {"decisions": [{"type": "approve"}]}
REJECT = {"decisions": [{"type": "reject", "message": "no"}]}


def test_write_tool_waits_for_approval_then_resumes_the_inner_checkpoint():
    _connect("github")
    run = _Run(("github_write_thing",))

    first = run.start()

    assert len(first["__interrupt__"]) == 1
    assert fake_mcp_server.CALLS == []  # nothing ran before the decision
    payload = first["__interrupt__"][0].value  # agent-inbox schema, unwrapped
    assert set(payload) == {"action_requests", "review_configs"}
    [request] = payload["action_requests"]
    assert (request["name"], request["args"]) == ("github_write_thing", {"name": "x"})
    assert "github_write_thing" in request["description"]
    assert payload["review_configs"] == [
        {"action_name": "github_write_thing", "allowed_decisions": ["approve", "reject"]}
    ]

    done = run.resume(APPROVE)

    assert "__interrupt__" not in done
    assert fake_mcp_server.CALLS == ["write_thing"]
    assert "wrote:x" in _reply(done)
    assert run.turns == [2, 4]  # resumed from the checkpoint: one more turn, not a restart


def test_rejected_write_runs_nothing_and_is_not_retried():
    _connect("github")
    run = _Run(("github_write_thing",))
    run.start()

    reply = _reply(run.resume(REJECT))

    assert fake_mcp_server.CALLS == []
    assert "User rejected the tool call" in reply
    assert run.turns == [2, 4]  # the inner model saw the rejection once and did not call again


def test_read_only_tool_runs_ungated():
    _connect("github")
    result = _Run(("github_read_thing",)).start()

    assert "__interrupt__" not in result
    assert fake_mcp_server.CALLS == ["read_thing"]


def test_unannotated_tool_is_gated_and_holds_the_whole_batch():
    _connect("github")
    run = _Run(("github_read_thing", "github_mystery", "github_write_thing"))

    first = run.start()

    gated = [a["name"] for a in first["__interrupt__"][0].value["action_requests"]]
    assert gated == ["github_mystery", "github_write_thing"]  # the read is not asked about
    assert fake_mcp_server.CALLS == []  # ...but nothing runs until the decisions are in

    run.resume({"decisions": [{"type": "reject", "message": "no"}, {"type": "approve"}]})

    assert sorted(fake_mcp_server.CALLS) == ["read_thing", "write_thing"]


def test_inner_prompt_says_a_rejected_call_was_not_run_and_is_not_retried():
    prompt = subagent.SYSTEM_PROMPT
    assert "rejected" in prompt and "not run" in prompt and "must not be retried" in prompt


def test_disabling_the_connection_mid_approval_drops_the_pending_call():
    _connect("github")
    run = _Run(("github_write_thing",))
    run.start()

    store.set_enabled("github", False)
    reply = _reply(run.resume(APPROVE))

    assert fake_mcp_server.CALLS == []  # approved, but never executed
    assert "dropped" in reply
    assert run.turns == [2]  # the inner agent was not re-entered
    assert run.pending() == ()  # and the thread is not stuck


def test_removing_the_pending_calls_connection_drops_it_even_if_another_remains():
    _connect("github")
    _connect("other")
    run = _Run(("github_write_thing",))
    run.start()

    store.delete("github")
    reply = _reply(run.resume(APPROVE))

    assert fake_mcp_server.CALLS == []
    assert "github_write_thing is not a valid tool" in reply


def test_removing_another_connection_leaves_the_pending_call_intact():
    _connect("github")
    _connect("other")
    run = _Run(("github_write_thing",))
    run.start()

    store.delete("other")
    run.resume(APPROVE)

    assert fake_mcp_server.CALLS == ["write_thing"]


def test_canary_read_only_hint_lives_at_the_metadata_path_the_gate_reads():
    """Fails if `langchain.mcp` moves or renames the path `read_only` depends on.

    The gate fails closed, so a rename would gate every tool rather than none --
    safe, but this is the test that tells you why.
    """

    async def tools():
        client = Client(StreamableHttpTransport(URL))
        async with MCPAdapter(ClientGroup({"gh": client})) as adapter:
            return {t.name: t for t in await adapter.list_tools()}

    listed = asyncio.run(tools())

    assert listed["gh_read_thing"].metadata["mcp"]["tool"]["annotations"]["read_only_hint"] is True
    assert listed["gh_write_thing"].metadata["mcp"]["tool"]["annotations"]["read_only_hint"] is False
    assert "annotations" not in listed["gh_mystery"].metadata["mcp"]["tool"]
    assert {n for n, t in listed.items() if subagent.read_only(t)} == {"gh_read_thing"}


def test_timeouts_come_from_the_servers_seam(monkeypatch):
    seen = {}
    real_client = subagent.Client

    def spy(transport, **kwargs):
        seen.update(kwargs)
        return real_client(transport, **kwargs)

    monkeypatch.setattr(subagent, "Client", spy)
    _connect("github")

    _Run().ask()

    assert seen == {"init_timeout": 30, "timeout": 60}


def test_a_connection_for_an_unknown_server_fails_with_a_reason():
    store.save_connection("gone", {"Authorization": "Bearer tok-gone"})

    reply = _Run().ask()

    assert "Unknown MCP server 'gone'" in reply
    assert "Unknown MCP server 'gone'" in store.load()["gone"]["lastError"]
