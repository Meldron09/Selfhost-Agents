"""`agent.web_search_gate.WebSearchGateMiddleware`, wired into the real graph.

tests/test_web_search_gate.py covers the two direct hook seams in isolation;
this file covers the wiring — `build_agent` → the orchestrator's own model
calls (the "## Research" prompt swap) and its `task` calls for
`subagent_type="web-search"` (the refusal short-circuit vs. the real
subagent), for both toggle states, driven end to end by a `ScriptedChatModel`
— no live Ollama, no live Tavily.
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from agent.config import Settings
from agent.graph import build_agent
from agent.scripted_model import ScriptedChatModel

_SETTINGS = Settings(ollama_model="test-model", ollama_context_window=8192)


def _tool_calls_seen(messages: list) -> list[str]:
    return [call["name"] for m in messages if isinstance(m, AIMessage) for call in (m.tool_calls or [])]


def _delegate_to_web_search(messages, tools):
    if "task" not in _tool_calls_seen(messages):
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "task",
                    "args": {
                        "description": "What is the current stable Python version?",
                        "subagent_type": "web-search",
                    },
                    "id": "orch-1",
                }
            ],
        )
    return AIMessage(content=str(messages[-1].content))


def _run(orchestrator_responder, settings: Settings, *, enable_web_search: bool | None):
    configurable: dict = {"thread_id": "t1"}
    if enable_web_search is not None:
        configurable["enable_web_search"] = enable_web_search

    agent = build_agent(model=ScriptedChatModel(responder=orchestrator_responder), settings=settings)
    return agent.invoke(
        {"messages": [HumanMessage(content="what's the latest Python version?")]},
        config={"configurable": configurable},
    )


# --- wrap_tool_call, wired: refusal vs. the real subagent -------------------


def test_web_search_delegation_is_refused_when_the_toggle_is_off():
    result = _run(_delegate_to_web_search, _SETTINGS, enable_web_search=False)

    tool_outputs = [str(m.content) for m in result["messages"] if getattr(m, "type", None) == "tool"]
    assert any("off for this run" in out for out in tool_outputs), tool_outputs
    # Refused before the real subagent ever ran — deepagents' own "does not
    # exist" wording, which is what an *unregistered* subagent would produce
    # instead, must not appear.
    assert not any("does not exist" in out for out in tool_outputs), tool_outputs


def test_web_search_delegation_is_refused_by_default_with_no_toggle_set():
    # A run submitted with no `enable_web_search` override at all — the
    # shape a client that hasn't wired the composer toggle yet would send.
    result = _run(_delegate_to_web_search, _SETTINGS, enable_web_search=None)

    tool_outputs = [str(m.content) for m in result["messages"] if getattr(m, "type", None) == "tool"]
    assert any("off for this run" in out for out in tool_outputs), tool_outputs


def test_web_search_delegation_reaches_the_real_subagent_when_the_toggle_is_on():
    def web_search_turn(messages, tools):
        if "web_search" in tools and "web_search" not in _tool_calls_seen(messages):
            return AIMessage(
                content="",
                tool_calls=[{"name": "web_search", "args": {"query": "current stable Python version"}, "id": "ws-1"}],
            )
        return AIMessage(content="Python 3.14 is current, per python.org.")

    def respond(messages, tools):
        if "task" in tools:
            return _delegate_to_web_search(messages, tools)
        return web_search_turn(messages, tools)

    result = _run(respond, _SETTINGS, enable_web_search=True)

    assert result["messages"][-1].content == "Python 3.14 is current, per python.org."
    tool_outputs = [str(m.content) for m in result["messages"] if getattr(m, "type", None) == "tool"]
    assert not any("off for this run" in out for out in tool_outputs), tool_outputs


# --- wrap_model_call, wired: the "## Research" prompt swap ------------------


def test_orchestrators_own_system_prompt_carries_the_available_fragment_when_on():
    def respond(messages, tools):
        visible = "\n".join(str(m.content) for m in messages)
        assert "Web search is off for this run" not in visible
        assert "delegate to the `web-search` subagent via `task`" in visible
        return AIMessage(content="ok")

    _run(respond, _SETTINGS, enable_web_search=True)


def test_orchestrators_own_system_prompt_carries_the_unavailable_fragment_when_off():
    def respond(messages, tools):
        visible = "\n".join(str(m.content) for m in messages)
        assert "Web search is off for this run" in visible
        return AIMessage(content="ok")

    _run(respond, _SETTINGS, enable_web_search=False)


def test_orchestrators_own_system_prompt_defaults_to_unavailable_with_no_toggle_set():
    def respond(messages, tools):
        visible = "\n".join(str(m.content) for m in messages)
        assert "Web search is off for this run" in visible
        return AIMessage(content="ok")

    _run(respond, _SETTINGS, enable_web_search=None)
