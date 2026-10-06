# deepagent-aegra

An independent deepagents multi-agent demo, self-hosted on `aegra` (mirroring
`agent-runtime`'s hosting approach, but not modifying it in place). See
`CONTEXT.md` for the domain vocabulary and `docs/adr/` for the decisions
behind it.

The orchestrator delegates every file it produces to `output-writer` ([#16](https://github.com/Meldron09/Selfhost-Agents/issues/16)),
every Attachment it's given to `file-reader` ([#17](https://github.com/Meldron09/Selfhost-Agents/issues/17)),
and research questions to `web-search` ([#18](https://github.com/Meldron09/Selfhost-Agents/issues/18))
whenever a run's `configurable.enable_web_search` is on. See the parent spec:
[#13](https://github.com/Meldron09/Selfhost-Agents/issues/13).

## Commands

- Install: `uv venv && uv pip install -r requirements.txt`
- Test: `pytest` (needs no credentials, reaches no live Ollama or Postgres —
  `tests/conftest.py` strips `OLLAMA_*`/`DATABASE_URL` from every test's env)
- Local infra: `docker compose up -d` (Postgres on :5432, plus the served
  `aegra-host` container — needs `.env` with `OLLAMA_MODEL`/
  `OLLAMA_CONTEXT_WINDOW` set; see `.env.example`)
- Check the infra: `python scripts/verify_stack.py` — PASS/FAIL per layer
  (file store, checkpointer, Ollama reachability)
- One run, streamed: `python -m agent.runner "hello"`
- Serve directly (no Docker): via `aegra-host/` (see its own README.md) —
  `aegra serve`, graph `agent` at `localhost:8000`

## Where things are

- `agent/graph.py` — the runtime: `make_agent()`, the entrypoint `aegra`
  loads via `AEGRA_GRAPH_TARGET`
- `agent/config.py` — every knob, read from env (`OLLAMA_MODEL`/
  `OLLAMA_CONTEXT_WINDOW` are required, fail-loud)
- `agent/model.py` — the Ollama model plane (no `MODEL_PROVIDER` dispatch —
  this project's model plane is settled, not pluggable)
- `agent/checkpointer.py` — Postgres thread state for standalone entry
  points (`agent.runner`, `scripts/verify_stack.py`) — `aegra serve` always
  persists through its own Postgres-backed saver instead
- `agent/scripted_model.py` — the test-only model, ported from
  `agent-runtime` (docs/adr/0005)
- `agent/state.py` — the custom `outputs`/`attachments` graph-state fields
  (docs/adr/0001)
- `agent/output_writer.py` — the `output-writer` subagent's four write tools
  (`write_xlsx`/`write_docx`/`write_pptx`/`write_txt`) and system prompt
- `agent/file_reader.py` — the `file-reader` subagent's five read tools
  (`read_pdf`/`read_xlsx`/`read_docx`/`read_pptx`/`read_txt`) and system
  prompt
- `agent/attachment_ack.py` — the middleware that surfaces `attachments` to
  the orchestrator's own model calls, so it can acknowledge them by name
  (docs/adr/0006)
- `agent/web_search.py` — the `web-search` subagent's two tools (`web_search`,
  `fetch_url`, Tavily-only) and system prompt
- `agent/web_search_gate.py` — `WebSearchGateMiddleware`: gates
  `web-search`'s per-run availability on `configurable.enable_web_search`
  (docs/adr/0007)
- `agent/mcp/subagent.py` — the `mcp` subagent: per delegation, connects the
  Connection Store's enabled Connections and runs an inner agent over their
  tools (async only — `agent.runner`'s sync path cannot delegate to it)
- `agent/subagents.py` — assembles the subagents the orchestrator's `task`
  tool can delegate to (all four always registered — see docs/adr/0007)
- `agent/files/` — the upload/download HTTP app (`/files`), mounted
  alongside the Agent Protocol routes via `aegra-host/http_app_adapter.py`
  (docs/adr/0004)
- `agent/skills/` — the Skill Library: install (zip), replace, delete and list Skills as plain
  folders under `SKILL_LIBRARY_DIR`, served as `/skills`, plus each Skill's `ui/` tree sandbox-ready at
  `/skills/{name}/ui/…` (issues #43, #44, #46; author guide: docs/skill-ui-contract.md)
- `agent/skill_run.py` — `SkillRunMiddleware`: a Skill Run's `SKILL.md` body, `fields` JSON, uploaded
  files and reference files (registered as Attachments) injected into the Orchestrator's prompt, from
  `configurable.skill_run`, and the one-active-Skill-Run-at-a-time queue with its `skill_run_status` stream events
  (issues #45, #48, docs/adr/0011)
- `scripts/verify_stack.py` — PASS/FAIL check of the file store, the
  checkpointer, and Ollama reachability — kept out of `pytest`
- `aegra-host/` — generic, project-agnostic `aegra serve` hosting scaffolding,
  an unmodified copy of `agent-runtime`'s (see its own README.md)
- `docker-compose.yml` (this file, repo root) — the project's own
  instantiation of that scaffolding: Postgres, the served container, and the
  dedicated file-store volume (docs/adr/0002)
