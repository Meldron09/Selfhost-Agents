# aegra-host

Generic, project-agnostic scaffolding for serving a `deepagents`/LangGraph-style
graph in production via [`aegra serve`](https://github.com/ibbybuilds/aegra)
(`pip install aegra-cli`). Nothing under this folder imports or path-references
a specific project — pointing it at a different graph is a config change (build
args + env vars), not a code change. See `docs/DECISIONS.md` in the hosting
project for why `aegra` was chosen here.

## Parameters

| Name | Where | Meaning |
| --- | --- | --- |
| `PROJECT_SRC_DIR` | Docker build arg | Path (relative to the build context, i.e. the hosted project's own repo root) to the project source to serve. Required. |
| `PROJECT_REQUIREMENTS` | Docker build arg | Path, relative to `PROJECT_SRC_DIR`, of the project's own `requirements.txt`. Default `requirements.txt`. |
| `AEGRA_GRAPH_DEPENDENCY_PATH` | Runtime env var | Absolute path *inside the container* where the project source lands (`/app/project` per this `Dockerfile`). Added to `sys.path` via `aegra.json`'s `dependencies` key. |
| `AEGRA_GRAPH_TARGET` | Runtime env var | `"<file relative to AEGRA_GRAPH_DEPENDENCY_PATH>:<exported factory or graph>"`, e.g. `src/app/graph.py:build_graph`. Required. |
| `AEGRA_HTTP_APP_DEPENDENCY_PATH` | Runtime env var | Same idea as `AEGRA_GRAPH_DEPENDENCY_PATH`, for the app `http_app_adapter.py` loads. Usually the same path. Required only if `AEGRA_HTTP_APP_TARGET` is set. |
| `AEGRA_HTTP_APP_TARGET` | Runtime env var | `"<file relative to AEGRA_HTTP_APP_DEPENDENCY_PATH>:<exported FastAPI instance or zero-arg factory>"`, e.g. `src/app/http.py:create_app`. Optional — a project with nothing to mount over HTTP leaves this unset; see below. |
| `DATABASE_URL` / `POSTGRES_*` | Runtime env var | Same keys `aegra` itself reads — an externally-managed Postgres instance. No Redis, single replica. |
| `HOST` / `PORT` | Runtime env var | Passed straight to `aegra serve`. Default `0.0.0.0:8000`. |
| (everything else) | `../.env`, loaded via `env_file` in `docker-compose.yml` | The hosted project's own `.env` (`MODEL_PROVIDER`, `STORAGE_BACKEND`, `TAVILY_API_KEY`, ...) is loaded into the container wholesale — optional, so a project without one still builds. The explicit keys above override same-named keys from that file. `docker run` has no equivalent — pass an env file with `--env-file` or repeat `-e` yourself. |

Model plane note: `MODEL_PROVIDER=ollama`'s `OLLAMA_BASE_URL` defaults to
`http://localhost:11434`, which from inside this container means the
container itself, not the host machine running the Ollama daemon.
`docker-compose.yml` overrides it to `http://host.docker.internal:11434`
unconditionally and adds the `extra_hosts` entry native Linux Docker Engine
needs to resolve that hostname (Docker Desktop resolves it already). Running
the image directly with `docker run` instead needs the same
`--add-host=host.docker.internal:host-gateway -e OLLAMA_BASE_URL=http://host.docker.internal:11434`.

## Build & run (from the hosted project's own repo root)

```bash
docker build -f aegra-host/Dockerfile \
  --build-arg PROJECT_SRC_DIR=. \
  --build-arg PROJECT_REQUIREMENTS=requirements.txt \
  -t my-project-aegra .

docker run --rm \
  -e AEGRA_GRAPH_DEPENDENCY_PATH=/app/project \
  -e AEGRA_GRAPH_TARGET=src/app/graph.py:build_graph \
  -e AEGRA_HTTP_APP_DEPENDENCY_PATH=/app/project \
  -e AEGRA_HTTP_APP_TARGET=src/app/http.py:create_app \
  -e DATABASE_URL=postgresql://user:pass@host:5432/db \
  -p 8000:8000 \
  my-project-aegra
```

Or via `docker-compose.yml` in this folder, which also brings up Postgres:

```bash
PROJECT_SRC_DIR=. AEGRA_GRAPH_TARGET=src/app/graph.py:build_graph \
  AEGRA_HTTP_APP_TARGET=src/app/http.py:create_app \
  docker compose -f aegra-host/docker-compose.yml up --build
```

## Why `graph_adapter.py` exists

`aegra-cli`'s graph loader
(`aegra_api.services.langgraph_service.LangGraphService._load_graph_from_file`)
treats a 0-arg callable graph export as a factory and calls it once, awaiting
the result if it's a coroutine — unlike `langgraph dev`, which accepted a
plain synchronous factory but never awaited it. A project whose graph module
exports a bare sync factory function (the common, idiomatic shape) fails to
load under `aegra` unmodified with `TypeError: object CompiledStateGraph
can't be used in 'await' expression`, because `aegra` treats any *callable*
export as needing this normalization, not just coroutine functions.

Re-verified against `aegra-cli` 0.10.5 (pinned version, 2026-09-23): 0.7.4
added per-request "config/runtime factory" support (`aegra_api.services.
graph_factory.classify_factory`), but that path only triggers for factories
that take 1+ parameters. `graph_adapter.py:graph` takes zero, so it is still
classified as a plain 0-arg factory, called once, exactly as under 0.6.0 —
confirmed by driving the actually-installed 0.10.5 `LangGraphService`
against this file directly, not just by reading its source.

`graph_adapter.py` is a small, generic shim aegra.json always points at
instead of the project's own graph module directly. It loads whatever
`AEGRA_GRAPH_TARGET` names, calls it if callable, and awaits the result only
if the result is itself awaitable — normalizing sync-factory, async-factory,
and already-compiled-graph shapes into what aegra expects, with no change to
the hosted project's own code. Re-verify this against the `aegra-cli` version
actually pinned in `requirements.txt` before assuming it still applies —
this is a version-specific workaround, not a documented contract. (0.10.5:
still applies, see above.)

## Mounting an extra app: `http_app_adapter.py` (optional)

`aegra`'s custom-app loader
(`aegra_api.core.app_loader.load_custom_app`) requires the `http.app` target
to already be a `FastAPI` *instance* — an `isinstance` check, stricter than
the plain-ASGI-app shape LangGraph Server accepted for a mounted app.
Once loaded, `aegra_api.main.create_app()` treats that instance as the whole
server: it grafts its own routers (health/assistants/threads/runs/store) onto
it, merges lifespans and exception handlers, and adds its own CORS/logging
middleware — the same "one process, one port, one health check" shape a
mounted app had under LangGraph Server, just built the other way around (the
custom app becomes the root; aegra extends it, rather than aegra staying root
and grafting the custom app in).

A project whose own app module exposes a *factory* (`create_app() -> FastAPI`,
recommended so every caller gets its own instance rather than one shared,
mutable app) needs something to call that factory before aegra ever sees the
result, since aegra only accepts an already-built instance. `http_app_adapter.py`
is that shim: it reads `AEGRA_HTTP_APP_TARGET`, loads the named export, calls
it if it's a factory function, and hands `aegra.json`'s `http.app` key
(`./http_app_adapter.py:app`) the resulting `FastAPI` instance. (A built
`FastAPI` instance is itself callable — the ASGI protocol — so the adapter
checks specifically for a function, not merely anything `callable()`.)

Not every hosted project has anything to mount here — a graph with no
artifacts/deliverables to serve back over HTTP has no use for this hook.
`entrypoint.sh` treats `AEGRA_HTTP_APP_TARGET` as the on/off switch: leave it
unset (and `AEGRA_HTTP_APP_DEPENDENCY_PATH` along with it) and the entrypoint
renders `aegra.json.no-http.template` instead — same `graphs` key, no `http`
key, `http_app_adapter.py` never imported. Set `AEGRA_HTTP_APP_TARGET` and
both vars become required, same fail-loud check as the graph vars.

## No Postgres migration step

`aegra serve` applies pending Alembic migrations automatically on startup
(`aegra_api.main`, `run_migrations_async`). That is fine at today's
single-replica deployment shape; revisit with an explicit migration step
before running multiple replicas of this image against the same database.

## Known limitations

Inherited by every project this scaffolding hosts, not swappable per-copy
today:

- **No authentication.** `aegra.json`'s `auth` key is never set — a
  deliberate, explicit gap, not an oversight, but every hosted project
  inherits it unless it adds its own auth layer in front of `aegra serve`.
- **Postgres, no Redis, single replica.** `docker-compose.yml` brings up one
  Postgres instance and one `aegra-host` container; see "No Postgres
  migration step" above for what changes before running more than one.
- **`requirements.txt` + pip only.** `Dockerfile`'s `PROJECT_REQUIREMENTS`
  build arg expects a pip requirements file; a project on Poetry/uv with only
  a `pyproject.toml` needs its own build-step change, not currently
  parameterized here.
