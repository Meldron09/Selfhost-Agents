# Can `deepagents`' graph state carry a custom field (e.g. `attachments`) that survives the Agent Protocol `POST /threads/{id}/runs` `input` contract, all the way down into a subagent's tools?

Researched: 2026-09-24, against `deepagents==0.7.18` (current `main`/latest release as of this date), cross-checked against `langchain==` the `langchain_v1` agents/middleware framework it's built on (from the same `langchain-ai` org, `main` branch), `langgraph` core (`main` branch), and `aegra` (`aegra/aegra`, `main` branch). The sibling project `agent-runtime` in this repo pins `deepagents==0.7.4` (2026-08-04); version-drift notes vs. that are called out inline where they matter.

---

## 1. Does `create_deep_agent` accept a custom state schema?

**Yes.** `create_deep_agent` in [`libs/deepagents/deepagents/graph.py`](https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/deepagents/graph.py) takes a keyword-only `state_schema: type[DeepAgentState] | None = None` parameter (line 284 of the current source). Its docstring is explicit:

> "Custom state schema for the agent graph. Must be a `TypedDict` subclass of `DeepAgentState` so the built-in `DeltaChannel` reducer on `messages` is preserved. Generally, prefer defining state extensions with middleware so the extra fields stay scoped to the hooks and tools that use them. When provided, this schema is used as the base graph schema and is merged with state schemas contributed by middleware. It is also forwarded when compiling declarative `SubAgent` specs for the `task` tool, so subagents see the same custom fields as the parent."

and gives the exact pattern the issue is asking about:

```python
from deepagents.graph import DeepAgentState

class MyState(DeepAgentState):
    page_url: str
    file_urls: list[str]

agent = create_deep_agent(model=..., state_schema=MyState)
```

`DeepAgentState` itself (same file, line 73) is minimal:

```python
class DeepAgentState(AgentState):
    """AgentState with `DeltaChannel` on messages to reduce checkpoint growth from O(N²) to O(N)."""
    messages: Required[Annotated[list[AnyMessage], DeltaChannel(_messages_delta_reducer, snapshot_frequency=50)]]
```

It's just `langchain.agents.AgentState` (defined in [`langchain/agents/middleware/types.py`](https://github.com/langchain-ai/langchain/blob/main/libs/langchain_v1/langchain/agents/middleware/types.py), line 349 — `messages`, `jump_to` (private), `structured_response`) with a smarter `messages` reducer. **`DeepAgentState` itself declares no `todos` or `files` field.** Those come from middleware (see §2), not from the base state class — an important correction to what the issue's framing assumed ("`AgentState` definition... what fields does it define beyond `messages` (e.g. `todos`, `files`)"): in the *current* version, neither is on the base class; both are middleware-contributed, and `todos` specifically is **not present by default at all** in 0.7.x (see version note below).

**Version note:** `state_schema` support was added in deepagents `0.6.6` (2026-05-28, per [CHANGELOG.md](https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/CHANGELOG.md): "Allow passing `state_schema` in `create_deep_agent`"), so it already existed in the `0.7.4` the sibling `agent-runtime` project pins — not a version-drift concern. What *did* change: `0.7.0` (2026-07-29) was a breaking release that **removed `TodoListMiddleware`, `write_todos`, and the `todos` state channel from the default middleware stack** ("`create_deep_agent` no longer includes `TodoListMiddleware` by default... Pass `middleware=[TodoListMiddleware()]` to restore them"). So on `deepagents>=0.7.0` (including both `0.7.4` and current `0.7.18`) there is no `todos` field unless you opt in with `middleware=[TodoListMiddleware()]`. `agent-runtime/agent/graph.py` corroborates this directly — its own comment says "deepagents 0.7.4 does not install planning or summarization itself" and it manually adds a todo middleware when `settings.enable_todos` is set.

## 2. If the schema is "fixed", can it still be extended? — yes, two supported mechanisms, and they compose

**Mechanism A — middleware-owned state slices (the officially preferred one).** `AgentMiddleware` (base class in `langchain/agents/middleware/types.py`, line 385) carries a class-level `state_schema: type[StateT] = _DefaultAgentState` attribute — "The schema for state passed to the middleware nodes." `deepagents`' own built-ins use exactly this: `FilesystemMiddleware` declares (`libs/deepagents/deepagents/middleware/filesystem.py`, line 1284):

```python
class FilesystemState(AgentState):
    files: Annotated[NotRequired[dict[str, FileData]], DeltaChannel(_file_data_delta_reducer, snapshot_frequency=50)]
```

and sets `self.state_schema = FilesystemState` on the middleware instance (line 1910). This is where the `files` field (per-path `{content, encoding, created_at?, modified_at?}`, from `backends/protocol.py` line 187) actually comes from — **not** from `DeepAgentState`.

`create_deep_agent` then merges every middleware's `state_schema` with the caller's `state_schema=` (`graph.py`, lines 939–941):

```python
state_schemas = [state_schema] if state_schema is not None else []
state_schemas.extend(mw.state_schema for mw in deepagent_middleware if getattr(mw, "state_schema", None) is not None)
private_state_keys = private_state_field_names(*state_schemas)
```

and passes the *caller-level* `state_schema` (or `DeepAgentState` as fallback) straight to `create_agent(..., state_schema=state_schema or DeepAgentState)` (line 970). `create_agent`'s own merge logic (`langchain/agents/factory.py`, `_resolve_schema`, line 488) is where the final compiled graph's state/input/output TypedDicts get built: it unions every field from every schema (the base `state_schema` + every middleware's `state_schema`), field-name last-write-wins, with the `InputSchema` variant additionally dropping any field annotated `OmitFromSchema(input=True, ...)` (i.e. `PrivateStateAttr` or `OmitFromInput`). **A plain, unannotated custom field is not dropped** — it flows straight into the graph's runtime input schema.

**Mechanism B — subclassing `DeepAgentState`/`AgentState` directly** and handing it to `state_schema=`, exactly as the docstring's own example shows. Both mechanisms compose: `create_deep_agent(state_schema=MyState, middleware=[SomeMiddlewareWithItsOwnStateSchema()])` merges all of them.

This maps onto standard LangGraph/`create_react_agent` practice — `StateGraph`/`create_react_agent`'s own `state_schema=` parameter and the `Annotated[..., reducer]` pattern for custom channels is the same mechanism `deepagents` builds on (LangGraph docs: [multi-agent / state schema guides](https://langchain-ai.github.io/langgraph/how-tos/state-model/)); `deepagents` just adds the middleware-level composition layer on top via the `langchain.agents.middleware` framework.

## 3. Does Agent Protocol / LangGraph Server (aegra) pass extra `input` keys through as-is, or validate/strip?

**Passed through opaquely, all the way down — no schema validation at either the aegra HTTP layer or inside the LangGraph engine's dispatch.** Traced concretely:

- **aegra's request model** (`libs/aegra-api/src/aegra_api/models/runs.py`, `RunCreate`): `input: dict[str, Any] | None` — untyped. No per-field Pydantic model, no reference to the graph's schema. The only validation is structural (`input`/`command` mutual exclusivity) — [github.com/aegra/aegra](https://github.com/aegra/aegra/blob/main/libs/aegra-api/src/aegra_api/models/runs.py).
- **aegra's run pipeline** (`services/run_preparation.py` → `services/run_executor.py::_resolve_input` → `services/graph_streaming.py`): the dict is carried through as `RunExecution.input_data`, and `_resolve_input` just returns `job.execution.input_data` unmodified. `graph_streaming.py` calls `graph.astream(input_data, ...)` / `graph.astream_events(input_data, ...)` directly with that raw dict — no intermediate schema check.
- **LangGraph's own engine** (`langgraph/pregel/_io.py::map_input`, [github.com/langchain-ai/langgraph](https://github.com/langchain-ai/langgraph/blob/main/libs/langgraph/langgraph/pregel/_io.py)) is what actually interprets the input dict at run time:
  ```python
  def map_input(input_channels, chunk):
      ...
      for k in chunk:
          if k in input_channels:
              yield (k, chunk[k])
          else:
              logger.warning(f"Input channel {k} not found in {input_channels}")
  ```
  Unknown keys are **not rejected** — they're silently dropped with only a `logger.warning` (no exception, nothing surfaced to the HTTP caller). Recognized keys (i.e., any channel present in the compiled graph's schema — which per §2 includes every non-omitted field from every merged middleware/custom state schema, not just `messages`) get applied normally.

**Net effect for this project:** a custom `attachments` field declared via `create_deep_agent(state_schema=MyState)` is a real channel on the compiled graph, so `POST /threads/{id}/runs` with `input={"messages": [...], "attachments": [...]}` will land in state correctly through aegra unmodified. But there is **no protocol-level guardrail** — if the field is ever *not* declared on the schema (e.g. schema drift, wrong graph deployed, typo in the field name), the request still returns 2xx/streams normally and the payload is just quietly dropped with a log line nobody sees from the client side. Frontend code (`agent-chat-ui`) gets no error signal for a mistyped or unwired custom input key — worth flagging as an operational risk for the wire-contract ticket, independent of which field name is chosen.

`get_input_schema()`/`get_input_jsonschema()` (`langgraph/pregel/main.py`, line 1014) exists and *would* let a server generate/enforce an OpenAPI-style schema from the compiled graph, but nothing in the aegra code path inspected calls it before dispatch — it's exposed for introspection/tooling, not wired into request validation.

## 4. Is a custom top-level field reachable inside a tool called by a deepagents *subagent* (via the `task` tool)?

**Yes, for declarative `SubAgent`s — by design, and confirmed in source at two separate points:**

**(a) Schema-level:** `SubAgentMiddleware` receives the same `state_schema` the caller passed to `create_deep_agent(...)` (`graph.py`, lines 883–884: `SubAgentMiddleware(..., state_schema=state_schema)`), and forwards it into `_build_task_tool(..., state_schema=state_schema)` → `create_sub_agent(spec, state_schema=state_schema)` → `create_agent(model, ..., state_schema=state_schema)` (`middleware/subagents.py`, lines 593–596, 702–706). So each declarative subagent's own compiled graph is built with the **same merged schema** as the parent — the `attachments` channel genuinely exists on the subagent's graph, not just the orchestrator's.

**(b) Data-flow level:** the `task` tool's `_validate_and_prepare_state` (`middleware/subagents.py`, lines 768–800) constructs the subagent's initial state as:
```python
subagent_state = {key: value for key, value in runtime.state.items() if key not in _EXCLUDED_STATE_KEYS | private_state_keys}
subagent_state["messages"] = [HumanMessage(content=description)]
```
`_EXCLUDED_STATE_KEYS = {"messages", "todos", "structured_response", "skills_metadata", _FORKED_CONTEXT_KEY}` (line 395). **A custom `attachments` field is not in this exclusion set**, so it is copied verbatim from the parent's `runtime.state` into the subagent's initial state — the entire parent state dict (minus messages/todos/structured_response/skills_metadata/private fields) is forwarded, not just a hand-picked subset. The built-in `files` channel (from `FilesystemMiddleware`, §2) is forwarded the exact same way — meaning **files already written into the virtual filesystem via the existing `files` state channel are already visible to every subagent today**, no custom field required for that specific case.

Only fields explicitly marked `PrivateStateAttr` (`OmitFromSchema(input=True, output=True)`) are excluded from this forwarding — and note this exclusion was itself a *bug fix* in a `0.7.x` patch release ("Keep fields marked with `PrivateStateAttr`, including fields declared through `create_deep_agent(state_schema=...)`, out of subagent inputs..." — [CHANGELOG.md](https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/CHANGELOG.md)), so on `0.7.18` this guarantee is solid, but it's worth knowing it wasn't always correctly enforced in earlier `0.7.x` patch versions.

**Inside the subagent, tools read this via `ToolRuntime`** (`from langchain.tools import ToolRuntime`, re-exported from `langgraph.prebuilt` alongside the older `InjectedState`/`InjectedStore` annotations — `libs/langchain_v1/langchain/tools/tool_node.py`). `deepagents`' own `task` tool implementation uses exactly this pattern (`runtime: ToolRuntime`, then `runtime.state.get(...)` / `runtime.state.items()`), and any custom tool given to a `file-reader` subagent can declare a `runtime: ToolRuntime` parameter and read `runtime.state["attachments"]` the same way — this is the modern, actively-used mechanism (superset of `InjectedState`, which still exists but is narrower).

**Important scope limits — this does *not* hold uniformly for all three subagent kinds `deepagents` supports:**
- **Declarative `SubAgent`** (the common case, what the wayfinder's file-reader/output-writer/web-search subagents would most likely be): full propagation as described above. ✅
- **`CompiledSubAgent`** (a pre-built, already-compiled runnable): does **not** inherit the parent's `state_schema` — per the `create_deep_agent` docstring, "compile those runnables with a compatible state schema if they need access to the same custom state fields" yourself. Data-flow-wise it still gets `runtime.state` minus excluded/private keys (line 788: `inherited = {k: v for k, v in runtime.state.items() if k not in _EXCLUDED_STATE_KEYS | private_state_keys}` for the forked-compiled branch), but if the compiled graph's own schema doesn't declare that channel, LangGraph's `map_input`-equivalent state-merge will drop it (same silent-drop behavior as §3, this time internal rather than at the HTTP boundary — per the `_ForkedContextState` docstring: "an undeclared key on a subagent's initial state is not tracked as a real channel").
- **`AsyncSubAgent`** (remote/background subagents, e.g. LangSmith deployments): uses "whatever schema is configured on the remote graph" — entirely independent; the field has to be threaded through explicitly (config/context or the remote graph's own schema), not inherited automatically at all.

## Bottom line

For the `deepagent-aegra/` wire-contract ticket: **a custom top-level `attachments` field on the orchestrator's state schema is fully supported end-to-end** — `create_deep_agent(state_schema=...)` → aegra's opaque `input` passthrough → LangGraph's channel-based merge → the `task` tool's state forwarding → `ToolRuntime.state` inside a subagent's own tools — provided the subagent in question is a **declarative `SubAgent`** (not `CompiledSubAgent`/`AsyncSubAgent`). No message-text/side-channel workaround is required; this is a first-class, documented pattern, not an assumption or a hack.

Two practical considerations for the actual design decision (not resolved here, just flagged):

1. **Consider reusing the existing `files` state channel instead of inventing `attachments`.** `FilesystemMiddleware`'s built-in `files: dict[str, FileData]` channel is already part of the merged schema by default (no custom `state_schema=` needed at all), is already excluded from `_EXCLUDED_STATE_KEYS` (so it's already forwarded to every declarative subagent today, in `0.7.18` as shipped), and is exactly "a structured field beyond `messages`" carrying a file reference/content into state. A custom `attachments` field would mainly earn its keep if you need richer metadata (e.g. an external URL/blob key rather than inline content, upload provenance, MIME type beyond what `FileData` carries) — `FileData` is `{content: str, encoding: "utf-8"|"base64", created_at?, modified_at?}`, i.e. designed for *content*, not a lightweight *pointer*.
2. **No protocol-level validation is a real risk, not just theoretical.** Because aegra/LangGraph silently drop unrecognized `input` keys with only a server-side log line (§3), a schema-name mismatch between what `agent-chat-ui` sends and what the deployed graph declares fails silently from the frontend's perspective — the run still starts, the file reference just never arrives. Whatever field name/shape is chosen, the wire-contract ticket should account for this (e.g. an explicit ack/echo of received attachments in the first streamed state update, so the frontend can detect a dropped field rather than trusting the request succeeded).
