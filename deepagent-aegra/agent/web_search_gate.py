"""`WebSearchGateMiddleware`: per-run toggle for the orchestrator's research delegation.

Issue #18's own acceptance criterion: all three subagents (`file-reader`,
`output-writer`, `web-search`) register structurally at build time, always --
no config-parameterized-graph-factory pattern. Whether `web-search` is
actually usable on a given run is instead a pure per-invocation config check,
made here, reading `configurable.enable_web_search` -- not baked in at graph-
build time the way, e.g., `agent-runtime` bakes its own research capability
in or out of the subagent roster based on whether a search provider is
configured at all.

Two seams, both reading the same flag:

* `wrap_model_call`/`awrap_model_call` swap the orchestrator's "## Research"
  prompt section between two fixed fragments on every model call, so a
  mid-thread toggle takes effect on the very next model call rather than
  needing a fresh thread. Read via `get_config()` (`langgraph.config`): the
  orchestrator's own model calls run with a plain `Runtime`, which -- unlike
  `ToolRuntime` -- carries no `.config` attribute at all.
* `wrap_tool_call`/`awrap_tool_call` intercept every `task` call aimed at
  `subagent_type == "web-search"` and short-circuit with a refusal
  `ToolMessage` when the flag is falsy for this run -- the real `web-search`
  subagent is never reached in that case. Read straight off
  `request.runtime.config`: a tool call's `ToolRuntime` (unlike the model
  call's plain `Runtime`) does carry `.config` directly, so there is no need
  to reach for `get_config()` here.

See docs/adr/0007-web-search-per-run-gate-via-middleware.md.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.config import get_config
from langgraph.types import Command

WEB_SEARCH_AVAILABLE = """## Research

For anything beyond a single trivial fact, delegate to the `web-search` subagent via `task` rather than guessing. Give it one self-contained question per call."""

WEB_SEARCH_UNAVAILABLE = """## Research

Web search is off for this run. Do not delegate to `web-search` — the call will be refused. If the request needs something you'd otherwise look up, say plainly that you can't search this run rather than answering from memory."""

_REFUSAL = (
    "web-search is off for this run (`enable_web_search` is false). This "
    "delegation was refused before reaching the web-search subagent -- tell "
    "the person you can't search this run rather than answering from memory."
)


def _enabled(configurable: dict | None) -> bool:
    return bool((configurable or {}).get("enable_web_search"))


def _research_fragment() -> str:
    config = get_config()
    configurable = config.get("configurable") if isinstance(config, dict) else None
    return WEB_SEARCH_AVAILABLE if _enabled(configurable) else WEB_SEARCH_UNAVAILABLE


def _with_research_section(request: ModelRequest) -> ModelRequest:
    fragment = _research_fragment()
    existing = request.system_message.text if request.system_message else ""
    combined = f"{existing}\n\n{fragment}" if existing else fragment
    return request.override(system_message=SystemMessage(content=combined))


def _is_web_search_delegation(tool_call: dict) -> bool:
    if tool_call.get("name") != "task":
        return False
    return (tool_call.get("args") or {}).get("subagent_type") == "web-search"


def _refusal(request: ToolCallRequest) -> ToolMessage:
    return ToolMessage(
        content=_REFUSAL,
        name="task",
        tool_call_id=request.tool_call["id"],
    )


class WebSearchGateMiddleware(AgentMiddleware):
    """Gates the orchestrator's research delegation on `configurable.enable_web_search`."""

    name = "WebSearchGateMiddleware"

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(_with_research_section(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        # aegra serve (and tests/test_server_compat.py's blockbuster guard)
        # invoke the graph asynchronously, and AgentMiddleware's default
        # awrap_model_call raises NotImplementedError unless overridden here
        # too -- see docs/adr/0006 for the same requirement on
        # AttachmentAcknowledgeMiddleware.
        return await handler(_with_research_section(request))

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        if _is_web_search_delegation(request.tool_call) and not _enabled(request.runtime.config.get("configurable")):
            return _refusal(request)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if _is_web_search_delegation(request.tool_call) and not _enabled(request.runtime.config.get("configurable")):
            return _refusal(request)
        return await handler(request)
