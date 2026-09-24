# Per-run config hooks in deepagents / LangGraph

Research for [issue #11](https://github.com/Meldron09/Selfhost-Agents/issues/11), following up on the `#7` decision that `deepagent-aegra`'s zero-arg, once-per-process `graph()` factory rules out compiling a separate graph per `configurable` value. Question: does `deepagents`' `create_deep_agent` (or the LangGraph middleware primitives it's built on) expose a per-invocation hook that can read `config["configurable"]` at call time and (1) vary the system prompt and (2) vary tool/subagent availability, without rebuilding the graph?

**Sources read directly** (installed into a scratch Python 3.11 venv and read from `site-packages`, not from memory — `pip install "deepagents>=0.7.4"` resolves to the versions below since no upper bound is pinned in `agent-runtime/requirements.txt`):

- `deepagents==0.7.18` — `deepagents/graph.py`, `deepagents/middleware/subagents.py`
- `langchain==1.4.2` — `langchain/agents/middleware/types.py`
- `langgraph==1.2.12` — `langgraph/runtime.py`, `langgraph/config.py`

## 1. Per-invocation dynamic system prompt

**Not supported by `create_deep_agent` itself.** `system_prompt: str | SystemMessage | None` (`deepagents/graph.py:275`) is consumed once, at build time, into `final_system_prompt` (`graph.py:639-648`), which is handed to `create_agent(system_prompt=final_system_prompt, ...)` — a static value baked into the compiled graph, exactly like `agent-runtime`'s `_system_prompt(settings)` today.

**But LangChain's middleware layer it's built on does support this, natively, via `wrap_model_call`.** `AgentMiddleware.wrap_model_call(self, request: ModelRequest, handler)` (`langchain/agents/middleware/types.py:503`) runs on *every* model call, not once at build time. `ModelRequest` (`types.py:87`) carries `.system_message`, `.state`, and `.runtime: Runtime[ContextT]`, and its `.override(system_message=...)` method (`types.py:203`) returns a new request with the system message swapped — exactly the "prompt is a callable/hook receiving state/config at each model call" shape the ticket asked about.

LangChain even ships a purpose-built decorator for this exact case: `@dynamic_prompt` (`types.py:1690-1777`), documented as "a convenience decorator that creates middleware using `wrap_model_call` specifically for dynamic prompt generation." Its own example (`types.py:1737-1741`) reads per-run data inside the function body and returns a new prompt string per call.

`deepagents` already uses this same hook itself, non-dynamically: `SubAgentMiddleware.wrap_model_call` (`deepagents/middleware/subagents.py:1004-1013`) appends a static "Available subagent types" fragment to `request.system_message` on every call via `request.override(system_message=...)`. It's proof the hook composes correctly inside a deepagents-built graph — it's just not reading `config` today.

**The `config["configurable"]` access wrinkle:** `ModelRequest.runtime` is a `langgraph.runtime.Runtime`, and `Runtime`'s own docstring is explicit — *"`Runtime` does not include `config`. To access `RunnableConfig`, inject it directly ... or use `get_config()` from `langgraph.config`"* (`langgraph/runtime.py:131-135`). So inside `wrap_model_call`, the way to reach `config["configurable"]["enable_web_search"]` is `from langgraph.config import get_config; config = get_config()` — a contextvar-backed accessor (`langgraph/config.py`) that works anywhere inside the run's call stack, not a parameter LangGraph hands the hook directly.

## 2. Conditional tool/subagent availability per invocation

**Two different levels, two different answers.**

**a) The `task` tool's subagent dispatch table is static, compiled once — not gateable per call as-is.** `_build_task_tool` (`deepagents/middleware/subagents.py:613-875`) builds `subagent_graphs: dict[str, Runnable]` (`subagents.py:866`) once, from the `subagents=[...]` passed to `create_deep_agent`, and closes over it in the `task`/`atask` functions. `task()` (`subagents.py:802-830`) checks `if subagent_type not in subagent_graphs` against this fixed dict — there is no per-call read of `config` in that check. This matches the ticket's premise: registering `web-search` structurally at build time (per `#7`) is required either way.

**b) But deepagents' own code proves the tool layer can still vary per call, via `ToolRuntime.config`.** `_get_subagent_response_format(runtime: ToolRuntime)` (`subagents.py:599-610`) reads `runtime.config.get("configurable", {})` inside `_select_subagent` (`subagents.py:753-766`), called on every `task` invocation, to swap in a different structured-output schema per run — a real, shipped precedent for "read `config["configurable"]` inside the task tool's own call path, per invocation, without rebuilding the graph." Unlike `Runtime` (used by `wrap_model_call`), `ToolRuntime` *does* carry `.config` directly (`langgraph/prebuilt/tool_node.py:1663-1703`, `ToolCallRequest.runtime: ToolRuntime` at `tool_node.py:149`) — no `get_config()` needed at this layer.

**c) `wrap_tool_call` is the general interception point for tool calls**, including calls to `task`. `AgentMiddleware.wrap_tool_call(request: ToolCallRequest, handler)` (`types.py:~690-750`) gets `request.tool_call` (name + args), `request.state`, `request.runtime` (a `ToolRuntime`, so `.config` is direct), and can skip calling `handler(request)` entirely to short-circuit with its own `ToolMessage`/`Command` — the mechanism for rejecting a specific subagent dispatch (`subagent_type == "web-search"`) for this run only, while leaving `web-search` registered in the compiled graph.

There is no native "vary the bound tool list to just this call" primitive for individual subagents inside `task` — `ModelRequest.override(tools=...)` (via `_ModelRequestOverrides`, `types.py:81`) can drop the whole `task` tool from a call, but that's all-or-nothing across every subagent, not per-subagent, so it's the wrong granularity for gating one of three subagents.

## 3. Smallest custom wrapper (concrete shape, grounded in the above)

A single custom `AgentMiddleware` subclass, added via `create_deep_agent(..., middleware=[WebSearchGateMiddleware()])` (merged into the stack by `_apply_custom_middleware`, `graph.py:204-238` — no separate compiled graph, same as any other user middleware):

```python
from langgraph.config import get_config
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse, ToolCallRequest

class WebSearchGateMiddleware(AgentMiddleware):
    """Per-run gate for the web-search subagent, read from config at call time."""

    def wrap_model_call(self, request: ModelRequest, handler) -> ModelResponse:
        enabled = get_config().get("configurable", {}).get("enable_web_search", False)
        text = RESEARCH_AVAILABLE if enabled else RESEARCH_UNAVAILABLE
        new_prompt = append_to_system_message(request.system_message, text)
        return handler(request.override(system_message=new_prompt))

    def wrap_tool_call(self, request: ToolCallRequest, handler):
        if request.tool_call["name"] == "task" and request.tool_call["args"].get("subagent_type") == "web-search":
            enabled = request.runtime.config.get("configurable", {}).get("enable_web_search", False)
            if not enabled:
                return ToolMessage(
                    content="web-search is disabled for this run.",
                    tool_call_id=request.tool_call["id"],
                )
        return handler(request)
```

- `wrap_model_call` reads config via `get_config()` (no `.config` on plain `Runtime`) and mirrors `agent-runtime`'s `RESEARCH_AVAILABLE`/`RESEARCH_UNAVAILABLE` string-swap pattern, but evaluated fresh on every call instead of once from `Settings`.
- `wrap_tool_call` reads config via `request.runtime.config` directly (`ToolRuntime` carries it) — same access pattern deepagents' own `_get_subagent_response_format` uses — and short-circuits before `handler(request)` ever reaches the static `task` tool's `subagent_graphs["web-search"]`, so the subagent stays registered (satisfying `#7`'s "always register structurally") but is refused for this run.
- Both hooks run per model/tool call inside the one graph compiled once by `graph_adapter.py:graph()`; nothing here requires a second `create_deep_agent()` call or a config-keyed cache of compiled graphs.

## Caveat on version drift

`agent-runtime/requirements.txt` pins `deepagents>=0.7.4` with no ceiling; the scratch venv resolved `0.7.18` (current latest as of 2026-09-24), not `0.7.4` exactly, because `deepagents` requires Python ≥3.11 and no older wheel was pulled down deliberately — pip took the newest version satisfying the constraint. The APIs cited here (`wrap_model_call`, `wrap_tool_call`, `ToolRuntime.config`, `_get_subagent_response_format`) are unlikely churn points, but `deepagent-aegra` should re-check `subagents.py`'s exact line numbers if it pins a different version.
