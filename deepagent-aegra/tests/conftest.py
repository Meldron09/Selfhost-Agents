"""Shared fixtures."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def imported_modules(py_file: Path) -> set[str]:
    """Every module a file imports, by static analysis — no execution.

    Shared by tests that assert import isolation
    (`test_aegra_host_portability.py`) so the same check can't drift.
    """
    tree = ast.parse(py_file.read_text(), filename=str(py_file))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def flattened_routes(routes):
    """`app.routes` entries for a router included via `include_router` (every
    aegra core router: health/assistants/threads/runs/store) come back as a
    lazy `_IncludedRouter` wrapper in this FastAPI version, not a flat `Route`
    — unwrap it via its `original_router` to see the routes it actually holds.
    Our own `/files` routes, registered straight onto the app, already come
    back as plain routes and pass through untouched.
    """
    flat = []
    for route in routes:
        if hasattr(route, "path"):
            flat.append(route)
        elif hasattr(route, "original_router"):
            flat.extend(flattened_routes(route.original_router.routes))
    return flat


_MODEL_ENV = ("OLLAMA_MODEL", "OLLAMA_CONTEXT_WINDOW", "OLLAMA_BASE_URL", "DATABASE_URL")


@pytest.fixture(autouse=True)
def _no_external_infrastructure(monkeypatch: pytest.MonkeyPatch):
    """No test may reach Ollama or a database, whatever the developer's shell holds.

    Autouse rather than opt-in: a test that forgets to pass `model=` explicitly
    must fail loudly (missing OLLAMA_MODEL) rather than silently reach whatever
    Ollama daemon happens to be running on the developer's machine.
    """
    for key in _MODEL_ENV:
        monkeypatch.delenv(key, raising=False)
