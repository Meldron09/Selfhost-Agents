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
