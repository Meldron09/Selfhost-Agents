"""`agent.graph`: orchestrator + `output-writer`, and the graph's entrypoints."""
from __future__ import annotations

import pytest
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from agent.config import Settings
from agent.graph import _build_middleware, _summarization_trigger, build_agent, make_agent
from agent.scripted_model import ScriptedChatModel


def test_make_agent_requires_ollama_env(monkeypatch: pytest.MonkeyPatch):
    """`make_agent` is the entrypoint `aegra serve` loads with no override —
    it must fail loudly rather than reach for a default model.
    """
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="OLLAMA_MODEL"):
        make_agent()


_TEST_SETTINGS = Settings(ollama_model="test-model", ollama_context_window=8192)
"""A `Settings` override with no fail-loud requirement — the seam tests use
instead of setting `OLLAMA_MODEL`/`OLLAMA_CONTEXT_WINDOW`, mirroring the
`model=` override's own role for the model plane (see agent/model.py)."""


def test_build_agent_with_overrides_needs_no_ollama_env():
    """The seam tests use instead of `MODEL_PROVIDER=fake` (this project has
    no such dispatch var — see agent/model.py).
    """
    agent = build_agent(model=ScriptedChatModel(final_text="hi"), settings=_TEST_SETTINGS)
    assert isinstance(agent, CompiledStateGraph)


def test_a_trivial_run_replies():
    agent = build_agent(model=ScriptedChatModel(final_text="hello there"), settings=_TEST_SETTINGS)
    result = agent.invoke(
        {"messages": [HumanMessage(content="hi")]},
        config={"configurable": {"thread_id": "t1"}},
    )
    assert result["messages"][-1].content == "hello there"


def test_output_writer_and_file_reader_are_registered_but_web_search_is_not():
    """This ticket's own scope: orchestrator + `output-writer` (#16) + `file-reader` (#17).

    `task`/`general-purpose` are deepagents' own built-in delegation
    capability and come for free regardless. `web-search` is a later ticket
    (#18) and must not be registered yet.
    """
    def responder(messages, tools):
        seen = [
            call["name"]
            for m in messages
            if isinstance(m, AIMessage)
            for call in (m.tool_calls or [])
        ]
        if "task" not in seen:
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "task",
                    "args": {"description": "search the web", "subagent_type": "web-search"},
                    "id": "c1",
                }],
            )
        return AIMessage(content="done")

    agent = build_agent(model=ScriptedChatModel(responder=responder), settings=_TEST_SETTINGS)
    result = agent.invoke(
        {"messages": [HumanMessage(content="hi")]},
        config={"configurable": {"thread_id": "t2"}},
    )
    tool_outputs = [m.content for m in result["messages"] if getattr(m, "type", None) == "tool"]
    assert any(
        "does not exist" in str(out) and "output-writer" in str(out) and "file-reader" in str(out)
        for out in tool_outputs
    ), tool_outputs


def test_a_given_checkpointer_persists_state_across_invocations():
    checkpointer = InMemorySaver()
    agent = build_agent(
        model=ScriptedChatModel(final_text="ok"), settings=_TEST_SETTINGS, checkpointer=checkpointer
    )
    config = {"configurable": {"thread_id": "t-persist"}}

    agent.invoke({"messages": [HumanMessage(content="first")]}, config=config)
    agent.invoke({"messages": [HumanMessage(content="second")]}, config=config)

    state = agent.get_state(config)
    contents = [m.content for m in state.values["messages"]]
    assert "first" in contents
    assert "second" in contents


# --- summarization (docs/adr/0003) ---------------------------------------


def test_summarization_trigger_uses_a_fraction_when_the_model_declares_a_profile():
    class _WithProfile:
        profile = {"max_input_tokens": 8192}

    assert _summarization_trigger(_WithProfile()) == ("fraction", 0.8)


def test_summarization_trigger_falls_back_to_absolute_tokens_with_no_profile():
    # ScriptedChatModel declares no `.profile` — same shape as any model
    # plane that doesn't expose one.
    assert _summarization_trigger(ScriptedChatModel()) == ("tokens", 150_000)


# --- attachment acknowledgment (#17) --------------------------------------


def test_orchestrator_sees_attachment_filenames_via_the_acknowledge_middleware():
    """`AttachmentAcknowledgeMiddleware` is wired into `build_agent`, not just
    unit-tested in isolation (tests/test_attachment_ack.py).
    """
    def responder(messages, tools):
        visible = "\n".join(str(m.content) for m in messages)
        assert "revenue.xlsx" in visible
        return AIMessage(content='I see "revenue.xlsx" was attached.')

    agent = build_agent(model=ScriptedChatModel(responder=responder), settings=_TEST_SETTINGS)
    result = agent.invoke(
        {
            "messages": [HumanMessage(content="what's the total?")],
            "attachments": [{"key": "a1.xlsx", "filename": "revenue.xlsx"}],
        },
        config={"configurable": {"thread_id": "t-ack"}},
    )
    assert "revenue.xlsx" in result["messages"][-1].content


def test_no_attachments_means_no_note_in_the_system_message():
    def responder(messages, tools):
        visible = "\n".join(str(m.content) for m in messages)
        assert "Attachments available" not in visible
        return AIMessage(content="hi there")

    agent = build_agent(model=ScriptedChatModel(responder=responder), settings=_TEST_SETTINGS)
    agent.invoke(
        {"messages": [HumanMessage(content="hi")]},
        config={"configurable": {"thread_id": "t-no-attachments"}},
    )


# --- REQUIRE_APPROVAL (docs/adr/0005, point 5: empty gate set) -----------


def test_require_approval_off_by_default_installs_no_hitl_middleware():
    # An interrupt hangs the graph until a client answers, so the default
    # must stay off — otherwise every unattended run and every other test
    # in this suite stops.
    settings = Settings(ollama_model="m", ollama_context_window=8192, require_approval=False)
    middleware = _build_middleware(settings, ScriptedChatModel())
    assert not any(isinstance(m, HumanInTheLoopMiddleware) for m in middleware)


def test_require_approval_true_installs_the_middleware_with_an_empty_gate_set():
    settings = Settings(ollama_model="m", ollama_context_window=8192, require_approval=True)
    middleware = _build_middleware(settings, ScriptedChatModel())
    hitl = [m for m in middleware if isinstance(m, HumanInTheLoopMiddleware)]
    assert len(hitl) == 1
    assert hitl[0].interrupt_on == {}


def test_a_scripted_run_with_require_approval_true_never_pauses():
    # Nothing is configured to gate, so even with REQUIRE_APPROVAL=true the
    # run must complete without an interrupt.
    agent = build_agent(
        model=ScriptedChatModel(final_text="hello"),
        settings=Settings(ollama_model="m", ollama_context_window=8192, require_approval=True),
    )
    result = agent.invoke(
        {"messages": [HumanMessage(content="hi")]},
        config={"configurable": {"thread_id": "t-approval"}},
    )
    assert "__interrupt__" not in result
    assert result["messages"][-1].content == "hello"
