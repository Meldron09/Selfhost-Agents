# Generic hosting image for any deepagents/LangGraph-compatible project,
# served by `aegra serve`. Portable: everything project-specific arrives as a
# build arg or a runtime env var — nothing in this file names a project.
#
# Build FROM THE HOSTED PROJECT'S OWN REPO ROOT, not from this directory —
# the image needs the project's source tree as build context:
#
#   docker build -f aegra-host/Dockerfile \
#     --build-arg PROJECT_SRC_DIR=. \
#     --build-arg PROJECT_REQUIREMENTS=requirements.txt \
#     -t my-project-aegra .
#
# Run:
#
#   docker run --rm \
#     -e AEGRA_GRAPH_DEPENDENCY_PATH=/app/project \
#     -e AEGRA_GRAPH_TARGET=src/app/graph.py:build_graph \
#     -e AEGRA_HTTP_APP_DEPENDENCY_PATH=/app/project \
#     -e AEGRA_HTTP_APP_TARGET=src/app/http.py:create_app \
#     -e DATABASE_URL=postgresql://user:pass@host:5432/db \
#     -p 8000:8000 my-project-aegra
#
# See README.md for what each parameter means and why graph_adapter.py /
# http_app_adapter.py exist.

FROM python:3.12-slim

ARG PROJECT_SRC_DIR
ARG PROJECT_REQUIREMENTS=requirements.txt

RUN test -n "$PROJECT_SRC_DIR" || { echo "PROJECT_SRC_DIR build-arg is required" >&2; exit 1; }

# gettext-base: entrypoint.sh's envsubst. build-essential: some project wheels
# still compile.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gettext-base \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY aegra-host/requirements.txt /app/aegra-host/requirements.txt
RUN pip install --no-cache-dir -r /app/aegra-host/requirements.txt

COPY ${PROJECT_SRC_DIR} /app/project
RUN pip install --no-cache-dir -r /app/project/${PROJECT_REQUIREMENTS}

COPY aegra-host/aegra.json.template aegra-host/graph_adapter.py aegra-host/http_app_adapter.py aegra-host/entrypoint.sh /app/aegra-host/
RUN chmod +x /app/aegra-host/entrypoint.sh

WORKDIR /app/aegra-host

# Never bake secrets. DATABASE_URL/POSTGRES_*, the model plane, and
# AEGRA_GRAPH_* all come from the environment at run time.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

ENTRYPOINT ["/app/aegra-host/entrypoint.sh"]
