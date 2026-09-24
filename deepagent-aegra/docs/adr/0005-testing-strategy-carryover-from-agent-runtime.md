---
status: accepted
---

# Testing strategy: what carries over from agent-runtime's test suite

deepagent-aegra is architecturally close enough to `agent-runtime` (same `aegra` hosting, same
credential-free/offline `pytest` convention) that its test suite is worth porting by inspection
rather than by decree: pulled in wholesale it would drag along concerns this project deliberately
dropped (object storage, the sandbox, `AttachmentIngestMiddleware`); ported blindly file-by-file it
would lose the precedent worth keeping (a scripted model, real-filesystem-backed assertions,
blockbuster event-loop guarding). This ADR draws that line, decided against `agent-runtime/tests/`
as it stood on 2026-09-24, resolving wayfinder ticket
[Decide: testing strategy for deepagent-aegra (#12)](https://github.com/Meldron09/Selfhost-Agents/issues/12).

## Decision

1. Port agent-runtime's `ScriptedChatModel` verbatim as deepagent-aegra's test-only model — kept
   orthogonal to the settled Ollama-only *production* model plane, which governs what serves runs,
   not what drives tests.
2. `file-reader`'s xlsx/docx/pptx/txt tests round-trip their input by calling `output-writer`'s own
   tools rather than maintaining static fixtures for those four formats. Only pdf keeps a static
   checked-in fixture (mirroring `tests/fixtures/sample.pdf`), since nothing in this project writes
   pdf.
3. Carries over near-verbatim: the `aegra-host/` adapter, config, and portability tests,
   `test_server_compat.py` (blockbuster), `test_checkpointer.py` — same architecture, same concern,
   unaffected by anything this project dropped.
4. Carries over as a pattern, new subject: `test_capabilities.py`'s real-backend/real-files/
   scripted-model-driven-end-to-end style becomes the `file-reader`/`output-writer` tool tests and a
   demo-script-equivalent acceptance test; its `_offered_subagents`/`_system_prompt_seen` helpers
   become the `WebSearchGateMiddleware` tests.
5. Drops entirely: `test_attachment_ingest.py` (superseded by the pointer-shaped wire contract,
   [#3](https://github.com/Meldron09/Selfhost-Agents/issues/3)), `test_outputs_guard.py` (no
   workspace-walk discovery — `outputs` state field only, #3), `test_storage.py` (no object-store
   dimension — local filesystem only), `test_human_in_the_loop.py`'s gated-tool cases (empty gate
   set, [#9](https://github.com/Meldron09/Selfhost-Agents/issues/9)).
6. New, no agent-runtime precedent: the upload/download HTTP app gets the same two-layer shape as
   agent-runtime's artifact-serving app (isolated via `TestClient`, then re-verified once mounted
   into `aegra`'s ASGI app — contract already settled on
   [#10](https://github.com/Meldron09/Selfhost-Agents/issues/10)).
   `WebSearchGateMiddleware` ([#11](https://github.com/Meldron09/Selfhost-Agents/issues/11)) gets
   both direct hook tests (`wrap_tool_call`/`wrap_model_call` called directly with a fake config)
   and one full-graph scripted-model test — the same two-layer treatment agent-runtime gives
   `AttachmentIngestMiddleware`, not the graph-only treatment it gives `deep-research`'s build-time
   gating, because this middleware is genuinely new machinery rather than a config-only variant of
   an existing pattern.
7. A `scripts/verify_stack.py`-equivalent, separate from `pytest`, covers deepagent-aegra's three
   live dependencies (Postgres round-trip, file-store round-trip, Ollama reachability) — keeping
   `pytest` itself fully offline, mirroring agent-runtime's own separation.
8. `test_server_compat.py`'s blockbuster guard extends to the new file-I/O paths
   (`resolve_attachment_bytes`/`store_output_bytes`, the upload/download HTTP app's route handlers)
   rather than getting a separate test — same aegra/ASGI hosting, same risk of a silent
   `Path.resolve()`-shaped regression (#6).

## Considered Options

For point 2, static fixtures for every format (matching pdf's treatment) were considered and
rejected: `output-writer`'s tools already produce well-formed xlsx/docx/pptx/txt, so a static
fixture would just duplicate what those tools generate, with no format-specific edge case a fixture
would catch that a round-trip wouldn't. Only pdf lacks a writer to round-trip through.

For point 6, testing `WebSearchGateMiddleware` only at the graph level (mirroring `deep-research`)
was considered and rejected: `deep-research`'s gating is build-time (present or absent depending on
`SEARCH_PROVIDER` at construction), so there is no per-invocation hook to test in isolation. This
middleware's gating is per-run and hook-based, so a graph-only test would leave the hooks themselves
unverified except as a side effect of a specific scripted conversation.
