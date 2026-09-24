#!/usr/bin/env sh
# Renders aegra.json from the template using this container's env vars, then
# execs `aegra serve`. Kept as a shell step (rather than baking a fixed
# aegra.json at build time) so the same image can be pointed at a different
# graph purely by changing the env vars a `docker run`/compose service sets.
set -eu

: "${AEGRA_GRAPH_DEPENDENCY_PATH:?AEGRA_GRAPH_DEPENDENCY_PATH must be set to the project source root inside the container}"
: "${AEGRA_GRAPH_TARGET:?AEGRA_GRAPH_TARGET must be set to '<file relative to AEGRA_GRAPH_DEPENDENCY_PATH>:<export>'}"

# The http.app mount is optional: a project with nothing to serve over HTTP
# (no artifacts app, say) sets neither var and gets aegra.json.no-http.template
# instead — same graph, no http key, http_app_adapter.py never even imported.
if [ -n "${AEGRA_HTTP_APP_TARGET:-}" ]; then
  : "${AEGRA_HTTP_APP_DEPENDENCY_PATH:?AEGRA_HTTP_APP_DEPENDENCY_PATH must be set to the project source root inside the container}"
  template=/app/aegra-host/aegra.json.template
else
  template=/app/aegra-host/aegra.json.no-http.template
fi

envsubst < "$template" > /app/aegra-host/aegra.json

export AEGRA_CONFIG=/app/aegra-host/aegra.json

exec aegra serve --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
