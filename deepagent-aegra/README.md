# deepagent-aegra

An independent deepagents multi-agent demo, self-hosted on `aegra` (mirroring
`agent-runtime`'s hosting approach, but not modifying it in place). See
`CONTEXT.md` for the domain vocabulary and `docs/adr/` for the decisions
behind it.

This scaffold ships a trivial graph with no subagents yet — `file-reader`,
`output-writer`, and `web-search` are later tickets. See the parent spec:
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
- `scripts/verify_stack.py` — PASS/FAIL check of the file store, the
  checkpointer, and Ollama reachability — kept out of `pytest`
- `aegra-host/` — generic, project-agnostic `aegra serve` hosting scaffolding,
  an unmodified copy of `agent-runtime`'s (see its own README.md)
- `docker-compose.yml` (this file, repo root) — the project's own
  instantiation of that scaffolding: Postgres, the served container, and the
  dedicated file-store volume (docs/adr/0002)
