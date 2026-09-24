# Web search: a per-run middleware gate, not a config-parameterized graph factory

`web-search` (agent/web_search.py) is the third subagent the orchestrator's `task` tool can delegate to, alongside `file-reader` and `output-writer`. Unlike those two, it is not always usable: a run controls whether it's on via `configurable.enable_web_search`, set per invocation by `agent-chat-ui`'s composer toggle — not a deployment-wide environment variable the way `agent-runtime` decides its own research capability (baked in or out of the subagent roster once, at graph-build time, based on whether a search provider is configured at all).

That difference rules out the build-time approach outright: a graph compiled once per process (`agent/graph.py`'s `make_agent()`, loaded once by `aegra serve`) cannot re-decide its own subagent roster per request without either compiling a second graph per config value (defeating the point of building once) or smuggling the flag in some side channel. So `web-search` registers structurally at build time, always (`agent/subagents.py`), exactly like `file-reader` and `output-writer` — and `WebSearchGateMiddleware` (agent/web_search_gate.py) makes the *registered* subagent's actual availability a runtime config check instead.

## Two seams, one flag

`WebSearchGateMiddleware` reads `configurable.enable_web_search` in two places, both added to the orchestrator's own middleware stack via `create_deep_agent(middleware=[...])` (agent/graph.py's `_build_middleware`):

- `wrap_model_call`/`awrap_model_call` swap the orchestrator's "## Research" prompt section between `WEB_SEARCH_AVAILABLE` and `WEB_SEARCH_UNAVAILABLE` on every model call, so a value set at the start of a run stays correct across the whole run, and a client that changes the toggle mid-thread takes effect on the model's very next call rather than requiring a fresh thread.
- `wrap_tool_call`/`awrap_tool_call` intercept every `task` call the orchestrator makes, check whether it targets `subagent_type == "web-search"`, and short-circuit with a refusal `ToolMessage` when the flag is falsy — the real `web-search` subagent (and, in turn, Tavily) is never invoked in that case. Any other `subagent_type` (`file-reader`, `output-writer`, or an unrecognized one deepagents itself will reject) passes straight through to the real `task` tool untouched.

Confirmed against the real middleware surface by source-level inspection: `ModelRequest.runtime` is a plain `langgraph.runtime.Runtime`, which carries no `.config` attribute at all — reading the flag inside `wrap_model_call` has to go through `langgraph.config.get_config()` instead. `ToolCallRequest.runtime` is a `langchain.tools.ToolRuntime`, which *does* carry `.config` directly (its own docstring lists `config: RunnableConfig` as one of the attributes injected alongside `state`/`tool_call_id`), so `wrap_tool_call` reads `request.runtime.config` rather than reaching for `get_config()` a second way for what is otherwise the identical read.

## Why gate the tool call and not just the prompt

Swapping the prompt section alone would only ever be a suggestion: nothing stops a model from ignoring `WEB_SEARCH_UNAVAILABLE`'s instruction and calling `task(subagent_type="web-search")` anyway — a model that has seen `web-search` in its own tool-call history from an earlier, search-enabled turn of the same thread has a concrete incentive to try again. The `wrap_tool_call` refusal is the actual enforcement point: it runs on every `task` call, prompt-following or not, and its refusal `ToolMessage` reports back through the same channel the model would otherwise have received `web-search`'s real synthesis on, so a badly-behaved model still gets an explicit, honest "no" it can relay rather than a silent bypass.

## Rejected: a second compiled graph per config value

Compiling two graphs (one with `web-search` in the roster, one without) and choosing between them per request was rejected for the same reason `agent-runtime`'s own build-time approach doesn't fit here: `aegra serve` calls the graph factory once per process, not once per request, so a second compiled graph would have to be built eagerly (doubling model/middleware construction cost for a capability most runs may not use) or lazily cached per config value (adding a cache-key/eviction concern to what is otherwise a stateless factory function) — machinery disproportionate to what a single per-call config read already solves.

## Known limitation: a model can still narrate around the refusal

The refusal `ToolMessage` stops the real subagent from ever running, but nothing stops the orchestrator's own model from paraphrasing a guess into its final reply instead of relaying the refusal honestly, the same class of risk `agent/graph.py`'s SYSTEM_PROMPT already accepts for `file-reader`/`output-writer` failure relaying. `WEB_SEARCH_UNAVAILABLE`'s explicit "say plainly that you can't search this run rather than answering from memory" instruction is the mitigation, and it's a prompt-following concern, not a plumbing one — left to model behavior and manual verification (issue #18's own "Demoable" acceptance criterion), not enforced in code.
