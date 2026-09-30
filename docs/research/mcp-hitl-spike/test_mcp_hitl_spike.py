"""Spike assertions for issue #27. Run from this directory:

    /path/to/deepagent-aegra/.venv/bin/python -m pytest -q -s test_mcp_hitl_spike.py
"""
from __future__ import annotations

import asyncio
import json

import fake_mcp
import pytest
from spike_lib import (
    NO_CONNECTIONS,
    FakeStore,
    HumanMessage,
    InMemorySaver,
    build_graph,
    inner_responder,
    outer_responder,
    tool_names,
    AIMessage,
)
from agent.scripted_model import ScriptedChatModel
from langgraph.types import Command

URL = fake_mcp.start()
URL_RO_DELETE = fake_mcp.start(delete_read_only=True)  # same server, but delete_thing is now read-only
APPROVE = {"decisions": [{"type": "approve"}]}
REJECT = {"decisions": [{"type": "reject", "message": "no"}]}


def conns(**kw):
    return {n: {"url": u, "token": "tok", "enabled": True} for n, u in (kw or {"gh": URL}).items()}


def connects() -> int:
    """Client connects seen server-side (fastmcp 4 'auto' mode speaks server/discover; legacy would be initialize)."""
    return fake_mcp.COUNTS["server/discover"] + fake_mcp.COUNTS["initialize"]


class Run:
    def __init__(self, store, calls=("gh_write_thing",), inner_checkpointer=None, tool_retries=0, outer=None):
        fake_mcp.reset()
        self.store, self.ol, self.il = store, [], []
        self.graph = build_graph(
            ScriptedChatModel(responder=(outer or outer_responder)(self.ol)),
            ScriptedChatModel(responder=inner_responder(self.il, calls)),
            store,
            inner_checkpointer=inner_checkpointer,
            tool_retries=tool_retries,
        )
        self.cfg = {"configurable": {"thread_id": "t"}}

    async def start(self):
        self.r = await self.graph.ainvoke({"messages": [HumanMessage("go")]}, self.cfg)
        return self.r

    async def resume(self, value):
        self.r = await self.graph.ainvoke(Command(resume=value), self.cfg)
        return self.r

    @property
    def final(self) -> str:
        return str(self.r["messages"][-1].content)


def arun(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- Q1 ----


def test_q1_approve_resumes_inner_checkpoint_not_restart():
    async def go():
        s = FakeStore(conns())
        run = Run(s)
        r = await run.start()
        # interrupt surfaced on the PARENT thread, write NOT executed, one connect/list so far
        assert len(r["__interrupt__"]) == 1
        assert fake_mcp.CALLS == []
        assert (connects(), fake_mcp.COUNTS["tools/list"], s.reads) == (1, 1, 1)
        assert len(run.il) == 1  # inner model: one pre-interrupt turn

        await run.resume(APPROVE)
        assert "__interrupt__" not in run.r
        assert fake_mcp.CALLS == ["write_thing"]
        # the runnable re-ran: re-read the store, reconnected, re-listed ...
        assert (connects(), fake_mcp.COUNTS["tools/list"], s.reads) == (2, 2, 2)
        # ... but the inner agent RESUMED: exactly one more model turn, seeing 4 messages (system, human, ai+call, tool result);
        # a restart would show a second `n_messages == 2` turn.
        assert [x["n_messages"] for x in run.il] == [2, 4]
        assert len(run.ol) == 2  # orchestrator model not replayed either (before task / after task)
        assert "wrote:x" in run.final

    arun(go())


def test_q1_reject_does_not_run_the_tool():
    async def go():
        run = Run(FakeStore(conns()))
        await run.start()
        await run.resume(REJECT)
        assert fake_mcp.CALLS == []
        assert "User rejected the tool call for `gh_write_thing` with reason: no" in run.final
        assert [x["n_messages"] for x in run.il] == [2, 4]

    arun(go())


def test_q1_gate_matrix_read_ungated_write_and_unannotated_gated():
    async def go():
        run = Run(FakeStore(conns()), calls=("gh_read_thing", "gh_mystery", "gh_write_thing"))
        await run.start()
        st = await run.graph.aget_state(run.cfg)
        gated = [a["name"] for a in st.tasks[0].interrupts[0].value["action_requests"]]
        assert gated == ["gh_mystery", "gh_write_thing"]  # read_thing is not gated; unannotated IS (fail-closed)
        assert fake_mcp.CALLS == []  # nothing ran yet, not even the read (whole AIMessage is held)
        await run.resume({"decisions": [{"type": "reject", "message": "no"}, {"type": "approve"}]})
        assert sorted(fake_mcp.CALLS) == ["read_thing", "write_thing"]  # mystery rejected

    arun(go())


@pytest.mark.parametrize(
    "inner_checkpointer, expect_inner_turns",
    [
        (None, 2),  # minimal construction: inherit
        (InMemorySaver, 2),  # own saver (fresh per call) is IGNORED: parent's saver in config wins
        (lambda: False, 3),  # checkpointer=False: no inner checkpoint -> inner turn REPLAYED on resume
    ],
    ids=["none", "own-saver-ignored", "False-replays"],
)
def test_q1_inner_checkpointer_variants(inner_checkpointer, expect_inner_turns):
    async def go():
        run = Run(FakeStore(conns()), inner_checkpointer=inner_checkpointer)
        await run.start()
        await run.resume(APPROVE)
        assert fake_mcp.CALLS == ["write_thing"]
        assert len(run.il) == expect_inner_turns

    arun(go())


def test_q1_two_parallel_mcp_delegations_need_interrupt_ids_to_resume():
    def two_tasks(log):
        def r(messages, tools):
            log.append(1)
            if "task" not in tool_names(messages):
                return AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "task", "args": {"description": "a", "subagent_type": "mcp"}, "id": "o1"},
                        {"name": "task", "args": {"description": "b", "subagent_type": "mcp"}, "id": "o2"},
                    ],
                )
            return AIMessage(content="outer done")

        return r

    async def go():
        run = Run(FakeStore(conns()), outer=two_tasks)
        r = await run.start()
        ids = [i.id for i in r["__interrupt__"]]
        assert len(ids) == 2
        # what agent-chat-ui sends today (bare value) is rejected by LangGraph:
        with pytest.raises(RuntimeError, match="multiple pending interrupts"):
            await run.resume(APPROVE)
        # one at a time by id works (the UI already has a tab per interrupt):
        r = await run.resume({ids[0]: APPROVE})
        assert fake_mcp.CALLS == ["write_thing"] and len(r["__interrupt__"]) == 1
        r = await run.resume({r["__interrupt__"][0].id: APPROVE})
        assert fake_mcp.CALLS == ["write_thing", "write_thing"] and "__interrupt__" not in r

    arun(go())


def test_q1_retry_after_transient_failure_restarts_inner_but_regates():
    """Orchestrator's ToolRetryMiddleware wraps `task`; a retry re-runs the inner agent FROM SCRATCH (not resume)."""

    async def go():
        fake_mcp.reset()
        il, boom = [], {"n": 0}
        base = inner_responder(il)

        def flaky(messages, tools):
            if getattr(messages[-1], "type", "") == "tool" and boom["n"] == 0:
                boom["n"] += 1
                raise RuntimeError("transient model failure")
            return base(messages, tools)

        g = build_graph(ScriptedChatModel(responder=outer_responder([])), ScriptedChatModel(responder=flaky),
                        FakeStore(conns()), tool_retries=2)
        cfg = {"configurable": {"thread_id": "t"}}
        await g.ainvoke({"messages": [HumanMessage("go")]}, cfg)
        r = await g.ainvoke(Command(resume=APPROVE), cfg)
        assert fake_mcp.CALLS == ["write_thing"]  # approved write ran once ...
        assert len(r["__interrupt__"]) == 1  # ... and the replayed plan asks for approval AGAIN (never silently repeats)

    arun(go())


# ---------------------------------------------------------------- Q2 ----


def test_q2_disabled_between_interrupt_and_resume_drops_pending_call():
    async def go():
        s = FakeStore(conns())
        run = Run(s)
        await run.start()
        s.conns["gh"]["enabled"] = False
        await run.resume(APPROVE)
        assert fake_mcp.CALLS == []  # approved write is NOT executed
        assert len(run.il) == 1  # inner agent never re-entered
        assert NO_CONNECTIONS in run.final
        assert (await run.graph.aget_state(run.cfg)).tasks == ()  # thread is not stuck

    arun(go())


def test_q2_pending_connection_deleted_but_another_remains():
    async def go():
        s = FakeStore(conns(gh=URL, other=URL))
        run = Run(s)
        await run.start()
        del s.conns["gh"]
        await run.resume(APPROVE)
        assert fake_mcp.CALLS == []
        assert "gh_write_thing is not a valid tool" in run.final  # ToolNode's own error, model-visible

    arun(go())


def test_q2_other_connection_deleted_pending_still_runs():
    async def go():
        s = FakeStore(conns(gh=URL, other=URL))
        run = Run(s)
        await run.start()
        del s.conns["other"]
        await run.resume(APPROVE)
        assert fake_mcp.CALLS == ["write_thing"]

    arun(go())


def test_q2_annotation_drift_changes_gate_count_fails_safe():
    async def go():
        s = FakeStore(conns())
        run = Run(s, calls=("gh_write_thing", "gh_delete_thing"))
        await run.start()
        s.conns["gh"]["url"] = URL_RO_DELETE  # delete_thing is now readOnlyHint=true -> 1 gated call, 2 decisions
        await run.resume({"decisions": [{"type": "approve"}, {"type": "reject", "message": "no"}]})
        # ToolRetry(on_failure=continue) turns the ValueError into a tool error; nothing executed
        assert fake_mcp.CALLS == []
        assert "Number of human decisions (2) does not match number of hanging tool calls (1)" in run.final

    arun(go())


def test_q2_edit_decision_is_refused_because_only_approve_reject_allowed():
    async def go():
        run = Run(FakeStore(conns()))
        await run.start()
        await run.resume({"decisions": [{"type": "edit", "edited_action": {"name": "gh_delete_thing", "args": {}}}]})
        assert fake_mcp.CALLS == []
        assert "is not allowed for tool 'gh_write_thing'" in run.final

    arun(go())


# ---------------------------------------------------------------- Q3 ----


def test_q3_metadata_path_and_fail_closed():
    from fastmcp import Client
    from fastmcp.client.group import ClientGroup
    from langchain.mcp import MCPAdapter
    from spike_lib import read_only

    async def go():
        async with MCPAdapter(ClientGroup({"gh": Client(URL, auth="tok")})) as a:
            return {t.name: t for t in await a.list_tools()}

    tools = arun(go())
    ann = lambda n: tools[n].metadata["mcp"].get("tool", {}).get("annotations")  # noqa: E731
    assert ann("gh_read_thing") == {"read_only_hint": True}  # snake_case, NOT readOnlyHint
    assert ann("gh_write_thing") == {"read_only_hint": False}
    assert ann("gh_mystery") is None  # no `annotations` key at all (model_dump(exclude_none=True))
    assert {n for n, t in tools.items() if read_only(t)} == {"gh_read_thing"}


# ---------------------------------------------------------------- Q4 ----


def test_q4_payload_reaches_ui_unchanged_via_aegra_stream_and_state():
    from aegra_api.core.serializers.langgraph import LangGraphSerializer
    from aegra_api.services.graph_streaming import stream_graph_events

    async def go():
        run = Run(FakeStore(conns()))
        ser = LangGraphSerializer()
        seen = []
        # exactly agent-chat-ui's submit options: streamMode ["values"], streamSubgraphs true
        async for ev, payload in stream_graph_events(
            run.graph, {"messages": [HumanMessage("go")]}, run.cfg, stream_mode=["values"], subgraphs=True
        ):
            if isinstance(payload, dict) and "__interrupt__" in payload:
                seen.append((ev, ser.serialize(payload["__interrupt__"])))
        evs = [e for e, _ in seen]
        assert "values" in evs  # root-level event: the one the SDK stores in stream.values.__interrupt__
        assert any(e.startswith("values|tools:") for e in evs)  # nested copy (SDK routes it to its subagent manager)
        root = dict(seen)["values"]
        assert len({json.dumps(p, sort_keys=True) for _, p in seen}) == 1  # same payload + same id at both levels
        v = root[0]["value"]
        assert set(v) == {"action_requests", "review_configs"}
        assert v["action_requests"][0]["name"] == "gh_write_thing"
        assert v["review_configs"][0]["allowed_decisions"] == ["approve", "reject"]

        # GET /threads/{id}/state (subgraphs=False) carries it on tasks[].interrupts too
        st = await run.graph.aget_state(run.cfg)
        assert ser.serialize_task(st.tasks[0])["interrupts"][0]["value"] == v

        # resume exactly as agent-chat-ui sends it
        async for _ in stream_graph_events(
            run.graph, Command(resume={"decisions": [{"type": "approve"}]}), run.cfg, stream_mode=["values"], subgraphs=True
        ):
            pass
        assert fake_mcp.CALLS == ["write_thing"]

    arun(go())
