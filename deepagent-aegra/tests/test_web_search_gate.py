"""`agent.web_search_gate`'s two direct hook seams, exercised with no model or
graph involved — the graph-level effect (the orchestrator's own system prompt
and its actual `task` delegation, for both toggle states) is exercised end to
end in tests/test_web_search_graph.py, driven by a `ScriptedChatModel`.
"""
from __future__ import annotations

import asyncio

from langchain.agents.middleware.types import ModelRequest, ToolCallRequest
from langchain.tools import ToolRuntime
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from agent.web_search_gate import (
    WEB_SEARCH_AVAILABLE,
    WEB_SEARCH_UNAVAILABLE,
    WebSearchGateMiddleware,
)

# --- wrap_model_call / awrap_model_call: the prompt-fragment swap ----------


def _model_request(system_message: SystemMessage | None = None) -> ModelRequest:
    return ModelRequest(
        model=None,
        messages=[HumanMessage(content="hi")],
        system_message=system_message,
        state={"messages": []},
    )


def test_wrap_model_call_appends_the_available_fragment_when_enabled(monkeypatch):
    monkeypatch.setattr(
        "agent.web_search_gate.get_config",
        lambda: {"configurable": {"enable_web_search": True}},
    )
    middleware = WebSearchGateMiddleware()
    seen = {}

    def handler(request):
        seen["text"] = request.system_message.text
        return None

    middleware.wrap_model_call(_model_request(SystemMessage(content="You are the orchestrator.")), handler)

    assert "You are the orchestrator." in seen["text"]
    assert WEB_SEARCH_AVAILABLE in seen["text"]
    assert WEB_SEARCH_UNAVAILABLE not in seen["text"]


def test_wrap_model_call_appends_the_unavailable_fragment_when_disabled(monkeypatch):
    monkeypatch.setattr(
        "agent.web_search_gate.get_config",
        lambda: {"configurable": {"enable_web_search": False}},
    )
    middleware = WebSearchGateMiddleware()
    seen = {}

    def handler(request):
        seen["text"] = request.system_message.text
        return None

    middleware.wrap_model_call(_model_request(), handler)

    assert WEB_SEARCH_UNAVAILABLE in seen["text"]
    assert WEB_SEARCH_AVAILABLE not in seen["text"]


def test_wrap_model_call_defaults_to_unavailable_when_the_flag_is_absent(monkeypatch):
    # No `enable_web_search` key at all — the same shape a run submitted with
    # no configurable override produces. Off by default, not on by default.
    monkeypatch.setattr("agent.web_search_gate.get_config", lambda: {"configurable": {}})
    middleware = WebSearchGateMiddleware()
    seen = {}

    middleware.wrap_model_call(_model_request(), lambda request: seen.__setitem__("text", request.system_message.text))

    assert WEB_SEARCH_UNAVAILABLE in seen["text"]


def test_awrap_model_call_swaps_the_fragment_too(monkeypatch):
    # aegra serve invokes asynchronously (docs/adr/0006's same requirement on
    # AttachmentAcknowledgeMiddleware) — the async path needs its own test,
    # not just inheritance from the sync one.
    monkeypatch.setattr(
        "agent.web_search_gate.get_config",
        lambda: {"configurable": {"enable_web_search": True}},
    )
    middleware = WebSearchGateMiddleware()
    seen = {}

    async def handler(request):
        seen["text"] = request.system_message.text
        return None

    async def main():
        await middleware.awrap_model_call(_model_request(), handler)

    asyncio.run(main())

    assert WEB_SEARCH_AVAILABLE in seen["text"]


# --- wrap_tool_call / awrap_tool_call: the refusal short-circuit -----------


def _tool_call_request(
    *,
    enabled: bool,
    subagent_type: str = "web-search",
    tool_name: str = "task",
) -> ToolCallRequest:
    runtime = ToolRuntime(
        state={},
        context=None,
        config={"configurable": {"enable_web_search": enabled}},
        stream_writer=lambda _delta: None,
        tool_call_id="c1",
        store=None,
    )
    return ToolCallRequest(
        tool_call={
            "name": tool_name,
            "args": {"description": "do the thing", "subagent_type": subagent_type},
            "id": "c1",
        },
        tool=None,
        state={},
        runtime=runtime,
    )


def test_wrap_tool_call_refuses_web_search_delegation_when_disabled():
    middleware = WebSearchGateMiddleware()
    request = _tool_call_request(enabled=False)

    def handler(_request):
        raise AssertionError("the real web-search subagent must not be reached")

    result = middleware.wrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    assert result.tool_call_id == "c1"
    assert "off" in result.content


def test_wrap_tool_call_allows_web_search_delegation_when_enabled():
    middleware = WebSearchGateMiddleware()
    request = _tool_call_request(enabled=True)
    sentinel = ToolMessage(content="ok", tool_call_id="c1")

    result = middleware.wrap_tool_call(request, lambda _request: sentinel)

    assert result is sentinel


def test_wrap_tool_call_ignores_task_calls_to_other_subagents():
    middleware = WebSearchGateMiddleware()
    request = _tool_call_request(enabled=False, subagent_type="output-writer")
    sentinel = ToolMessage(content="ok", tool_call_id="c1")

    result = middleware.wrap_tool_call(request, lambda _request: sentinel)

    assert result is sentinel


def test_wrap_tool_call_ignores_non_task_tool_calls():
    middleware = WebSearchGateMiddleware()
    request = _tool_call_request(enabled=False, tool_name="read_pdf")
    sentinel = ToolMessage(content="ok", tool_call_id="c1")

    result = middleware.wrap_tool_call(request, lambda _request: sentinel)

    assert result is sentinel


def test_awrap_tool_call_refuses_web_search_delegation_when_disabled():
    middleware = WebSearchGateMiddleware()
    request = _tool_call_request(enabled=False)

    async def handler(_request):
        raise AssertionError("the real web-search subagent must not be reached")

    result = asyncio.run(middleware.awrap_tool_call(request, handler))

    assert isinstance(result, ToolMessage)
    assert "off" in result.content


def test_awrap_tool_call_allows_web_search_delegation_when_enabled():
    middleware = WebSearchGateMiddleware()
    request = _tool_call_request(enabled=True)
    sentinel = ToolMessage(content="ok", tool_call_id="c1")

    async def handler(_request):
        return sentinel

    result = asyncio.run(middleware.awrap_tool_call(request, handler))

    assert result is sentinel
