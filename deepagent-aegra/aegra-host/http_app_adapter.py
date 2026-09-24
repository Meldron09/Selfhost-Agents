"""Generic factory-shape adapter for aegra's `http.app` mount hook.

`aegra`'s custom-app loader (`aegra_api.core.app_loader.load_custom_app`)
requires the named export to already be a `FastAPI` *instance* — an
`isinstance` check, stricter than the plain-ASGI-app shape LangGraph Server
accepted for a mounted app. A project's own app module instead exposes a
factory (`create_app() -> FastAPI`), so every caller builds its own instance
rather than sharing one mutable app across callers (adding middleware to a
shared instance twice, once per caller, is exactly the kind of bug a fresh
instance avoids). This adapter calls that factory once, at import time, and
hands aegra the fresh result; an export that is already a built instance is
passed through unchanged.

Configured entirely through environment variables, so this file carries no
reference to any specific project:

  AEGRA_HTTP_APP_DEPENDENCY_PATH  - project source root
  AEGRA_HTTP_APP_TARGET           - "<file relative to that root>:<export name>",
                                     naming a `FastAPI` instance or a
                                     zero-argument factory that returns one.

`AEGRA_HTTP_APP_DEPENDENCY_PATH` is also put on `sys.path` here, not only
used to resolve the target file. `aegra.json`'s own `dependencies` entry
does the equivalent for the *graph* — but only lazily, inside
`LangGraphService.__init__`, first triggered from the request lifespan. This
adapter runs eagerly, at `aegra_api.main` import time — plain synchronous
module import, before uvicorn's event loop exists, let alone before that
lifespan has ever run — so a target module with ordinary absolute imports of
its own project's other modules (`from agent.artifacts.store import ...`,
say) would otherwise fail with `ModuleNotFoundError` even though the exact
same dependency path is configured. Being outside any event loop is also why
this file's blocking file I/O needs no `blockbuster` guard the way
graph_adapter.py's does: that adapter's `graph()` coroutine really is
awaited on the server's own loop; this module's top-level code never is.

Mirrors graph_adapter.py's shape for the analogous graph-side hook. Mounting
an extra app is optional: entrypoint.sh only requires the two env vars below,
and therefore only renders aegra.json with this file named as the `http.app`
target, when AEGRA_HTTP_APP_TARGET is set. A hosted project with nothing to
mount leaves both env vars unset and this module is never imported.
"""
from __future__ import annotations

import importlib.util
import inspect
import os
import sys
from pathlib import Path
from typing import Any


def _load_export() -> Any:
    dependency_path = os.environ.get("AEGRA_HTTP_APP_DEPENDENCY_PATH")
    if not dependency_path:
        raise RuntimeError("AEGRA_HTTP_APP_DEPENDENCY_PATH is not set")

    target = os.environ.get("AEGRA_HTTP_APP_TARGET")
    if not target:
        raise RuntimeError("AEGRA_HTTP_APP_TARGET is not set")

    file_part, sep, attr = target.partition(":")
    if not sep or not attr:
        raise ValueError(f"AEGRA_HTTP_APP_TARGET must be '<file>:<export>', got {target!r}")

    if dependency_path not in sys.path:
        sys.path.insert(0, dependency_path)

    file_path = (Path(dependency_path) / file_part).resolve()
    if not file_path.exists():
        raise FileNotFoundError(f"AEGRA_HTTP_APP_TARGET file not found: {file_path}")

    spec = importlib.util.spec_from_file_location("aegra_host._http_app_target", file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load http app target from {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, attr):
        raise AttributeError(f"{file_path} has no attribute {attr!r}")
    return getattr(module, attr)


def _build_app() -> Any:
    export = _load_export()
    # `callable(export)` is not the right test: a built FastAPI/Starlette
    # instance is itself callable (that's the ASGI protocol — `__call__(self,
    # scope, receive, send)`), so it would pass a bare `callable()` check and
    # get invoked with zero arguments. Only a *function* (the factory shape)
    # gets called; anything else is assumed to already be the app instance.
    if inspect.isfunction(export) or inspect.ismethod(export):
        return export()
    return export


app = _build_app()
"""What `aegra.json`'s `http.app` key points at: `./http_app_adapter.py:app`."""
