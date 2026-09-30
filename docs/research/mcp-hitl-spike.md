# Spike: HITL approval gate inside the `mcp` CompiledSubAgent

Research for [issue #27](https://github.com/Meldron09/Selfhost-Agents/issues/27) (child of the MCP support map, #19). It tests the design settled in #26: `mcp` is a `CompiledSubAgent` whose runnable rebuilds a `ClientGroup` per `task` delegation, lists tools, then runs a fresh inner `create_agent` with a `HumanInTheLoopMiddleware` that gates every tool whose MCP annotations lack `readOnlyHint: true` (approve/reject only).

**Verdict: yes, with changes.** The `CompiledSubAgent` design holds; the fallback (an orchestrator-level middleware that owns the group) is **not** needed. Interrupt, approve/reject and resume work end to end through the real `build_agent` stack (real `create_deep_agent`, all real orchestrator middleware, `mcp` appended to the roster). On resume the runnable re-runs, which re-reads the store, reconnects and re-lists. The fresh inner agent resumes from its checkpoint rather than restarting, provided the runnable passes its ambient `config` into the inner `ainvoke` and the inner agent is not compiled with `checkpointer=False`. A disabled Connection drops the pending call, and a gate mismatch fails safe. The payload reaches agent-chat-ui unchanged. There are four things to carry into implementation (listed under "Changes to the design in #26"). The one that matters most: **agent-chat-ui cannot resume when two `mcp` delegations are pending at once**, because it sends a bare resume value and LangGraph requires interrupt ids when more than one is pending. The other open item is that Q5 (GitHub's real annotations over the wire) is **unverified** against the hosted remote server, since no PAT was available. Its strongest public evidence is strong, though: every one of the 125 tools pinned at `v1.12.2` carries an explicit `readOnlyHint`, and an upstream test enforces that.

Legend: **(ran)** executed in this spike, **(source)** read from installed or upstream source, **(unverified)** not checked.

Everything runnable is in `docs/research/mcp-hitl-spike/`. To reproduce, run `cd docs/research/mcp-hitl-spike && <venv>/bin/python -m pytest -q test_mcp_hitl_spike.py`. Result: **15 passed** (ran).

## Versions (from `importlib.metadata` in `deepagent-aegra/.venv`, Python 3.12.13)

| Package | Version |
|---|---|
| `deepagents` | 0.7.18 |
| `langgraph` | 1.2.12 (`langgraph-checkpoint` 4.2.0) |
| `langchain` | 1.4.2 (`langchain.mcp` is beta) |
| `langchain-core` | 1.6.4 |
| `fastmcp` | 4.0.10 |
| `mcp` (SDK) | 2.2.0 |
| `aegra-api` | 0.10.5 |
| `@langchain/langgraph-sdk` (agent-chat-ui `node_modules`) | 1.11.0 |
| agent-chat-ui | recorded gitlink `2cafb7a95224c159475e58712605d09a061fff8f` |

Setup and its limits: the fake MCP server is a real streamable-HTTP `FastMCP` app on a loopback port, wrapped in a pure-ASGI counter that records JSON-RPC `method`s (`fake_mcp.py`). It has four tools: `read_thing` (`readOnlyHint=True`), `write_thing` (`False`), `mystery` (no annotations) and `delete_thing` (`False`, destructive). The checkpointer is `InMemorySaver`. **Postgres and a live `aegra serve` were not run**; the aegra-side checks call aegra's own `stream_graph_events` and serializer against the real graph.

fastmcp 4 connects with `server/discover` in its default "auto" mode, not `initialize`, so the connect counter sums both (ran).

## Minimal working construction

This is `spike_lib.build_mcp_subagent`, annotated.

```python
async def run(state, config):
    conns = store.enabled()                       # re-read on EVERY run, including resume
    if not conns:
        return {"messages": [AIMessage(NO_CONNECTIONS)]}
    group = ClientGroup({n: Client(c["url"], auth=c["token"], init_timeout=30, timeout=60)
                         for n, c in conns.items()})
    async with MCPAdapter(group) as adapter:      # one connect per run; closes when the interrupt unwinds
        tools = await adapter.list_tools()
        gate = {t.name: {"allowed_decisions": ["approve", "reject"]} for t in tools if not read_only(t)}
        inner = create_agent(inner_model, tools=tools, system_prompt=INNER_PROMPT,
                             middleware=[HumanInTheLoopMiddleware(interrupt_on=gate)],
                             name="mcp-inner")    # NO checkpointer= argument
        # `config` is the ambient parent config: it carries the parent's checkpointer, thread_id
        # and checkpoint_ns, which is what lets interrupt() resume this fresh inner agent.
        return await inner.ainvoke({"messages": state["messages"]}, config)

mcp = {"name": "mcp", "description": "...", "runnable": RunnableLambda(run)}

def read_only(tool) -> bool:                      # fail-closed
    ann = (tool.metadata or {}).get("mcp", {}).get("tool", {}).get("annotations", {})
    return ann.get("read_only_hint") is True
```

## Q1. Does interrupt → approve/reject → resume work end to end?

**Yes (ran).** `test_q1_approve_resumes_inner_checkpoint_not_restart` and `test_q1_reject_does_not_run_the_tool` give these counts. The inner scripted model records how many messages it saw on each call. The system prompt accounts for one message.

| | After first invoke (interrupted) | After `Command(resume=approve)` |
|---|---|---|
| Write executed on the server | no (`CALLS == []`) | yes, once (`['write_thing']`) |
| Client connects (`server/discover`) | 1 | **2** |
| `tools/list` calls | 1 | **2** |
| `Store.enabled()` reads | 1 | **2** |
| Inner model calls (messages seen) | 1 (`[2]`) | 2 (`[2, 4]`) |
| Orchestrator model calls | 1 | 2 (before `task`, after `task`) |

- **The runnable re-runs on resume.** It re-reads the store, reconnects and re-lists, exactly as #26 worried.
- **The inner agent resumes from its checkpoint.** It makes exactly one more model call and sees 4 messages (system, human, AI with the call, tool result). A restart would show a second 2-message call. The pre-interrupt LLM turn is not replayed. The orchestrator model is not replayed either.
- **Reject** executes nothing. The inner agent's next turn sees `User rejected the tool call for gh_write_thing with reason: no`.
- **The connection is not held across the approval wait.** It closes when `GraphInterrupt` unwinds the `async with`. So a slow approval costs no open session, only one reconnect and re-list on resume.
- **Gate matrix** (`test_q1_gate_matrix_*`). `[read_thing, mystery, write_thing]` in one AI message produces one interrupt with `action_requests = [gh_mystery, gh_write_thing]`. `read_thing` is not gated, and the **unannotated tool is gated (fail-closed)**. Nothing runs before the decision, not even the read, because the whole AI message is held. `edit` decisions are refused with `ValueError: ... not allowed for tool 'gh_write_thing'`, so approve/reject-only holds server-side too.

**The checkpoint/namespace subtlety (ran, plus source).** The runnable is a plain async function that builds a fresh inner agent each call. It still resumes correctly because the inner graph is invoked inside the `tools` node, with the ambient `config` passed in. LangGraph derives the subgraph checkpoint namespace from that config and finds the parent's saver in `config["configurable"]`. The checkpointer variants:

| Inner `create_agent(checkpointer=...)` | Result (ran) | Why (source) |
|---|---|---|
| omitted (`None`) | resumes, 2 inner model calls | inherits the parent's saver from config |
| an own `InMemorySaver`, **fresh per call** | resumes, 2 inner model calls | an own saver is **ignored**: `langgraph/pregel/main.py:2579-2586` uses `config[CONF][CONFIG_KEY_CHECKPOINTER]` whenever it is present |
| `checkpointer=False` | "works", but **3** inner model calls | `main.py:2579-2580` turns checkpointing off. Resume replays the whole inner turn, so a real LLM could propose a *different* call than the one the human approved. **Must not be used.** |

So the minimal construction is: pass `config` through, and leave `checkpointer` unset. This also requires the parent to have a checkpointer, which aegra always supplies; standalone `agent.runner` uses `sync_checkpointer()`.

**Two parallel `task(mcp)` delegations in one orchestrator turn (ran).** This produces **two simultaneous interrupts**, which is where agent-chat-ui breaks (see Q4).
- A bare `resume={"decisions": [...]}` raises `RuntimeError: When there are multiple pending interrupts, you must specify the interrupt id when resuming.`
- `resume={interrupt_id: {...}}` works, both all-at-once and one at a time. Resuming one id runs that delegation's write and leaves the other pending.

**A retry restarts the inner agent (ran, `test_q1_retry_after_transient_failure_*`).** The orchestrator stack includes `ToolRetryMiddleware` (`agent/graph.py`, `tool_retries=2`), which wraps `task`. If the inner agent fails transiently after an approved write ran, the retry re-runs `task(mcp)` from scratch, not as a resume. The approved write ran once. The replayed plan raised a **fresh interrupt** for the same write. The gate therefore prevents a silent duplicate, but the person sees a second approval prompt for something already done. This is acceptable for now, and it is flagged as fog below.

**Sync path (ran).** `RunnableLambda(async_fn).invoke` raises `TypeError: Cannot invoke a coroutine function synchronously`, and so do the MCP tools (async-only, see the prior spike). deepagents' sync `task` calls `subagent.invoke` (`subagents.py:829`), while `atask` calls `ainvoke` (`subagents.py:859`). aegra is async, so served runs are fine. `agent/runner.py` uses sync `agent.stream`, so it cannot delegate to `mcp` without a sync shim or a documented limitation.

## Q2. Enabled set changes between interrupt and resume

All cases were run with `tool_retries=0` unless noted, and all fail safe: nothing the person did not approve ran.

| Scenario (test) | Behaviour (ran) |
|---|---|
| **Only Connection disabled or deleted** (`test_q2_disabled_*`) | The runnable returns the "no MCP connections are enabled" message and never re-enters the inner agent. The approved write is **not executed** (`CALLS == []`). The thread is not stuck (`tasks == ()`), and the orchestrator relays the message. The inner checkpoint is left orphaned. |
| **Pending call's Connection removed, another remains** | The inner agent resumes but the tool no longer exists. `after_model` finds no gate entry for the name (`human_in_the_loop.py:457`) and does not consume the decision. `ToolNode` answers `Error: gh_write_thing is not a valid tool, try one of [other_...]` (`langgraph/prebuilt/tool_node.py:109`), and the model sees that. Nothing executes. |
| **A different Connection removed, pending's remains** | Normal resume, the write runs as approved. |
| **Annotation drift changes the gated-call count** (for example a server flips a second pending tool to read-only) | `after_model` re-evaluates the gate against the fresh tool list. Two decisions for one gated call raises `ValueError: Number of human decisions (2) does not match number of hanging tool calls (1)` (`human_in_the_loop.py:484`). With `tool_retries=0` the orchestrator's `ToolRetryMiddleware(on_failure="continue")` turns it into a tool error, and nothing executes. With `tool_retries=2` the retry restarts the inner agent and re-gates it (see the Q1 retry note). |

**Recommended behavior: drop the pending call with a clear message, never auto-execute.** It needs no extra machinery, because the gate is rebuilt from the fresh store read and tool list on resume. A call runs only if the tool still exists *and* a decision was supplied for it. Two small wording changes make the drop legible to the model and the person:
1. The zero-enabled message says that any approval-pending action was dropped. The spike uses `No MCP connections are enabled, so nothing was run. Any action that was awaiting approval has been dropped.` (this covers the most likely case, the person turning the Connection off in Settings while deciding).
2. The inner agent's system prompt tells it that a call reported as rejected, invalid or not found was **not run** and must not be retried silently (`INNER_PROMPT` in `spike_lib.py`).

The annotation-drift `ValueError` is left as is (fail-safe, rare, no code). Catching it to produce a nicer message is possible but not worth building now.

## Q3. `readOnlyHint` on converted tools, and fail-closed

**Stable path (ran, `test_q3_*`; source `langchain/mcp/tools.py:183-205`):**

```
tool.metadata["mcp"]["tool"]["annotations"]["read_only_hint"]      # snake_case, NOT "readOnlyHint"
```

Observed on the converted tools:

```
gh_read_thing    {"annotations": {"read_only_hint": true},  "_meta": {...}}
gh_write_thing   {"annotations": {"read_only_hint": false}, "_meta": {...}}
gh_mystery       {"_meta": {...}}                                # no "annotations" key at all
gh_delete_thing  {"annotations": {"read_only_hint": false, "destructive_hint": true}, ...}
```

- The conversion is `tool.annotations.model_dump(exclude_none=True)` (`tools.py:195`, no `by_alias`), so keys are snake_case. `#26`'s "lack `readOnlyHint: true`" must be implemented as `read_only_hint is True`.
- An unannotated tool, or one with a null hint, has **no `annotations` key**. Because the check is `.get(...) is True`, it ends up **gated**. The test asserts that the only ungated tool is `gh_read_thing`.
- The tool name in `interrupt_on` is the prefixed public name (`gh_...`), the same name the model calls, so the gate key and the call name always agree.
- `langchain.mcp` is beta, so the path is "stable" only for these pinned versions (`langchain` 1.4.2). The fail-closed shape means an upstream rename would gate *everything* rather than nothing. A one-line test like `test_q3_*` catches that on upgrade.

## Q4. Does the payload render in agent-chat-ui unchanged, from inside a subagent?

**Yes (ran for the server side, source for the UI).**

**What the server sends.** Driving the real graph through aegra's own `stream_graph_events` with agent-chat-ui's exact submit options (`streamMode: ["values"]`, `streamSubgraphs: true`, `agent-chat-ui/src/components/thread/index.tsx:261-262`), the interrupt arrives as:
- a **root-namespace** `values` event, `{"__interrupt__": [{"value": {...}, "id": "9bbc..."}]}`, and
- a nested copy on `values|tools:<uuid>` with the same id and the same payload.

`GET /threads/{id}/state` (`aget_state`, `subgraphs=False`, aegra's serializer) also carries it on `tasks[0].interrupts`, with task name `tools` (ran, `test_q4_*`). The value is the stock HITL request with no wrapping or renaming:

```json
{"action_requests":[{"name":"gh_write_thing","args":{"name":"x"},
   "description":"Tool execution requires approval\n\nTool: gh_write_thing\nArgs: {'name': 'x'}"}],
 "review_configs":[{"action_name":"gh_write_thing","allowed_decisions":["approve","reject"]}]}
```

**How the UI picks it up (source).**
- `useStream().interrupt` comes from `values.__interrupt__`, else from the thread head's `tasks[].interrupts` (`@langchain/langgraph-sdk` `dist/react/stream.lgp.js:522-535`, `dist/ui/interrupts.js`).
- The SDK's stream manager routes `values|<ns>` events whose namespace contains `tools:` to its *subagent* manager instead of the stream values (`dist/ui/manager.js:414`, `isSubagentNamespace` at `dist/ui/subagents.js:18-22`). The nested copy is therefore ignored for `stream.values`.
- The **root** `values` event is what sets `__interrupt__` (`manager.js:436`). It exists here, so the nested origin does not hide the interrupt. On a page reload the `tasks[].interrupts` path covers it.
- `ai.tsx:125` reads `thread.interrupt` and `ai.tsx:89` gates on `isAgentInboxInterruptSchema` (`src/lib/agent-inbox-interrupt.ts:4-52`). I ran that real guard (bundled with esbuild, `ui_guard_check.mjs`) on the captured payload, both raw and after the SDK's `normalizeInterruptForClient`. It returned `true` for the object and for `[object]`, and `false` if `review_configs` is missing (ran).
- The interrupt is shown when it belongs to the last message (`isLastMessage`). While the approval is pending, the last message is the orchestrator's AI message carrying the `task` call, so it renders the same way an orchestrator-level HITL would.
- `createDefaultHumanResponse` (`agent-inbox/utils.ts:89-150`) builds options from `allowed_decisions`. An `["approve","reject"]` config shows exactly Approve and Reject, with no edit box. Reject requires a non-empty reason (`buildDecisionFromState`), and the reason is forwarded as `reject.message`.

**How the UI resumes (source), and whether HITL accepts it.** `use-interrupted-actions.tsx:87-96` and `thread-actions-view.tsx:170-232` call `thread.submit({}, {command: {resume: {decisions: [...]}}})`. A single interrupt with N actions sends N decisions in order. This matches HITL's `interrupt(...)["decisions"]` exactly (`human_in_the_loop.py:481`). aegra forwards it as `Command(resume=cmd.get("resume"))` (`aegra_api/utils/run_utils.py:31-35`), and the resume in `test_q4_*` is that exact shape (ran). The resume submit does not pass `streamSubgraphs`, only the regular send does. Nested token streaming is therefore off during the resumed run, which is cosmetic.

**One real gap (ran plus source): multiple pending interrupts.** `ThreadView` shows a tab per interrupt when `interrupts.length > 1` (`agent-inbox/index.tsx:70`), but every submit path sends the bare `resume: {decisions}` with no interrupt id. LangGraph rejects that with `multiple pending interrupts ... must specify the interrupt id` (ran). Two `task(mcp)` calls in one orchestrator turn, which `ToolNode` runs in parallel, produce exactly this. The fix is small and UI-side: when more than one interrupt is pending, send `resume: {[activeInterrupt.id]: {decisions}}`. The spike shows one-at-a-time by id works. This is the same latent problem for any HITL inside parallel subagents and is not specific to `mcp`.

## Q5. Does the GitHub MCP server annotate its tools with `readOnlyHint`?

**Unverified against the live remote server, strongly supported by source.** There was no PAT to use: no `GITHUB_*` or `GH_*` variables are set in the environment, and `deepagent-aegra/.env` has only model, database, file-store and Tavily keys. I did not use `gh auth token`, which is a different credential. No secret was written anywhere.

Evidence from the pinned release, `github/github-mcp-server` tag `v1.12.2` (the version pinned in `docs/research/pin-mcp-registry-entries.md`), read from source, unauthenticated:

- **All 125 tool snapshots** under `pkg/github/__toolsnaps__/*.snap` carry an explicit `annotations.readOnlyHint`: **60 `true`, 65 `false`, 0 missing** (computed by script). The 65 `false` tools are the mutating ones: `create_issue`, `add_issue_comment`, `merge_pull_request`, `delete_file`, `push_files`, `create_or_update_file`, `delete_repository`, `issue_write`, `projects_write`, `actions_run_trigger`, `update_pull_request`, and so on. The 60 `true` tools are `get_*`, `list_*`, `search_*`, `*_read`, `projects_get` and `projects_list`.
- The Go source sets it per tool, for example `pkg/github/issues.go:831` (`ReadOnlyHint: true`) and `:1373` (`ReadOnlyHint: false`).
- The repo enforces it: `pkg/github/tools_validation_test.go` requires every tool to have `Annotations` with `ReadOnlyHint` explicitly set (header comment at line 24, `require.NotNil(... Annotations ...)` at lines 40-42), plus a `TestToolReadOnlyHintConsistency` at line 153.
- The README documents `--read-only` and `GITHUB_READ_ONLY` ("only offer read-only tools"), which uses the same hint.

**What is not verified.**
1. The hosted remote at `https://api.githubcopilot.com/mcp/` is a separately deployed build. I expect it to advertise the same annotations, since it is the same codebase, but I did not list its tools.
2. Whether the hint survives the Go SDK's JSON encoding on the wire. Go `omitempty` could drop a `false`. That is harmless here, because an absent hint is treated as gated.
3. Whether the PAT-scoped tool list at runtime matches the snapshot set.

The fail-closed gate makes the worst case safe: a missing or dropped hint gates the tool. The only thing Q5 can change is how *noisy* the gate is. A reading agent that finds half the read tools unannotated would prompt for every read. **To finish Q5:** with a PAT, run `MCPAdapter(ClientGroup({"github": Client(StreamableHttpTransport("https://api.githubcopilot.com/mcp/", auth=PAT))})).list_tools()` and count tools with `metadata["mcp"]["tool"]["annotations"]["read_only_hint"] is True`, which is about 60 if the remote matches.

## Changes to the design in #26

The `CompiledSubAgent` design stands. Carry these into the implementation slices and ADR-0008's Consequences:

1. **Construction rules (hard requirements).** The runnable must be `async` and pass its ambient `config` into the inner `ainvoke`. The inner `create_agent` must not set `checkpointer=False` (omit it). Pass only `{"messages": ...}` into the inner agent. `build_subagents()` taking the model is unchanged.
2. **Gate predicate spelling.** Replace "annotations lack `readOnlyHint: true`" with the exact path: `metadata["mcp"]["tool"]["annotations"]["read_only_hint"] is True`, snake_case, so an unannotated tool has no `annotations` key at all. Add one test that fails if `langchain.mcp` changes that path.
3. **Disable-mid-approval behavior.** Make the zero-enabled reply say any approval-pending action was dropped, and add the "rejected / not valid / not found means not run" line to the inner prompt. No other code is needed; a disabled or removed Connection drops the call.
4. **agent-chat-ui: resume by interrupt id when more than one is pending.** Needed because parallel `task(mcp)` calls are possible (see Q1 and Q4). Until then, a prompt line telling the orchestrator to delegate to `mcp` once per turn only reduces the odds.
5. **Sync path.** `agent/runner.py` (sync `agent.stream`) cannot delegate to an async-only `mcp` runnable. Decide between a documented limitation and a sync shim. Sync graph tests must not delegate to `mcp`.

## Suggested follow-ups (new tickets or fog)

- **Ticket: agent-chat-ui keyed resume for multiple pending interrupts.** Small, contained, and independent of the rest.
- **Fog: `ToolRetryMiddleware` around `task(mcp)` restarts the inner agent and re-prompts for writes that already ran.** It is safe but annoying. The fix would be excluding `mcp` from retry, but the middleware matches on the tool name `task`, so it cannot single out one subagent. It could need a retry predicate or a different wrapper.
- **Unverified, needs a PAT: the real remote server's tool annotations** (Q5), plus a Postgres-backed run against a live `aegra serve` to confirm resume with `AsyncPostgresSaver`.
