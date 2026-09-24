"""`agent.graph`: the trivial (no subagents yet) graph and its entrypoints."""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph

from agent.graph import build_agent, make_agent
from agent.scripted_model import ScriptedChatModel


def test_make_agent_requires_ollama_env(monkeypatch: pytest.MonkeyPatch):
    """`make_agent` is the entrypoint `aegra serve` loads with no override —
    it must fail loudly rather than reach for a default model.
    """
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="OLLAMA_MODEL"):
        make_agent()


def test_build_agent_with_a_model_override_needs_no_ollama_env():
    """The seam tests use instead of `MODEL_PROVIDER=fake` (this project has
    no such dispatch var — see agent/model.py).
    """
    agent = build_agent(model=ScriptedChatModel(final_text="hi"))
    assert isinstance(agent, CompiledStateGraph)


def test_a_trivial_run_replies():
    agent = build_agent(model=ScriptedChatModel(final_text="hello there"))
    result = agent.invoke(
        {"messages": [HumanMessage(content="hi")]},
        config={"configurable": {"thread_id": "t1"}},
    )
    assert result["messages"][-1].content == "hello there"


def test_no_project_subagents_are_registered():
    """This ticket's own scope: 'a trivial (no subagents yet) deepagents graph'.

    `task`/`general-purpose` are deepagents' own built-in delegation
    capability and come for free regardless — what this ticket must NOT
    register yet are `file-reader`/`output-writer`/`web-search` (later
    tickets #15-#18).
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
                    "args": {"description": "read a file", "subagent_type": "file-reader"},
                    "id": "c1",
                }],
            )
        return AIMessage(content="done")

    agent = build_agent(model=ScriptedChatModel(responder=responder))
    result = agent.invoke(
        {"messages": [HumanMessage(content="hi")]},
        config={"configurable": {"thread_id": "t2"}},
    )
    tool_outputs = [m.content for m in result["messages"] if getattr(m, "type", None) == "tool"]
    assert any("does not exist" in str(out) for out in tool_outputs), tool_outputs


def test_a_given_checkpointer_persists_state_across_invocations():
    checkpointer = InMemorySaver()
    agent = build_agent(model=ScriptedChatModel(final_text="ok"), checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "t-persist"}}

    agent.invoke({"messages": [HumanMessage(content="first")]}, config=config)
    agent.invoke({"messages": [HumanMessage(content="second")]}, config=config)

    state = agent.get_state(config)
    contents = [m.content for m in state.values["messages"]]
    assert "first" in contents
    assert "second" in contents
