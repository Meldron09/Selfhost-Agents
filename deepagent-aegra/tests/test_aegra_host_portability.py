"""`aegra-host/` must stay droppable into a different project unmodified.

Two different ways this could regress, so two different checks: a Python file
could `import` something project-specific (the `agent/artifacts` precedent:
`test_artifacts.py::test_the_app_imports_nothing_from_the_graph_or_deepagents`),
or any file — a Dockerfile comment, a compose default, a template — could
hardcode a path back into this specific project instead of taking it as a
parameter. See aegra-host/README.md and .scratch/migrate-to-aegra/issues/02-*.md.
"""
from __future__ import annotations

from pathlib import Path

from conftest import imported_modules

REPO_ROOT = Path(__file__).resolve().parent.parent
AEGRA_HOST = REPO_ROOT / "aegra-host"

# Substrings that could only appear by hardcoding a reference back into this
# project's own specific paths or symbols. Deliberately narrower than "deepagents"
# or "LangGraph" — aegra-host/'s own docs legitimately name the *class* of
# project it hosts; what must never appear is *this* project's own path shape.
_BANNED_SUBSTRINGS = (
    "agent-runtime",
    "agent/graph.py",
    "agent.graph",
    "skill-backend-api",
    "make_agent",
)


def _all_files() -> list[Path]:
    """Only what's actually checked in — not `__pycache__` a prior import wrote."""
    return [
        p
        for p in AEGRA_HOST.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    ]


def test_the_folder_is_not_empty():
    assert _all_files(), "aegra-host/ has no files to check"


def test_no_file_hardcodes_a_reference_back_into_this_project():
    offenders = []
    for path in _all_files():
        text = path.read_text(errors="ignore")
        for banned in _BANNED_SUBSTRINGS:
            if banned in text:
                offenders.append((path.relative_to(REPO_ROOT), banned))
    assert not offenders, offenders


def test_no_python_file_imports_this_projects_code():
    banned_modules = ("agent", "deepagents", "api")
    offenders = []
    for py_file in AEGRA_HOST.rglob("*.py"):
        for module in imported_modules(py_file):
            root = module.split(".", 1)[0]
            if root in banned_modules:
                offenders.append((py_file.relative_to(REPO_ROOT), module))
    assert not offenders, offenders


def test_no_python_file_imports_anything_outside_the_standard_library():
    """A dependency here would tie the adapter to whatever happens to be
    installed alongside this project, rather than staying pip-installable
    with `aegra-cli` alone (see aegra-host/requirements.txt).
    """
    import sys

    stdlib = sys.stdlib_module_names
    offenders = []
    for py_file in AEGRA_HOST.rglob("*.py"):
        for module in imported_modules(py_file):
            root = module.split(".", 1)[0]
            if root not in stdlib and root != "__future__":
                offenders.append((py_file.relative_to(REPO_ROOT), module))
    assert not offenders, offenders
