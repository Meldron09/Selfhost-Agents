"""`aegra-host/`'s config: parameterized, and carries nothing `aegra` can't use.

`aegra.json` has no equivalent of `langgraph.json`'s custom `checkpointer.path`
hook — `aegra`'s `checkpointer` config key only controls TTL/sweep behavior and
always uses its own Postgres-backed saver (see docs/DECISIONS.md and
`tests/test_checkpointer.py`). A template carried over from `langgraph.json`
with that key still present would silently do nothing under `aegra`, so this
asserts the template's actual content rather than merely documenting the
difference.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AEGRA_HOST = REPO_ROOT / "aegra-host"

_PLACEHOLDER = re.compile(r"\$\{[A-Z_][A-Z0-9_]*\}")


def _load_template() -> dict:
    return json.loads((AEGRA_HOST / "aegra.json.template").read_text())


def test_the_template_is_valid_json():
    config = _load_template()
    assert isinstance(config, dict)


def test_the_graph_dependency_path_is_a_placeholder_not_a_hardcoded_path():
    config = _load_template()
    deps = config["dependencies"]
    assert deps == ["${AEGRA_GRAPH_DEPENDENCY_PATH}"]
    for dep in deps:
        assert _PLACEHOLDER.fullmatch(dep), f"{dep!r} is not a parameterized placeholder"


def test_the_graph_entry_points_at_the_generic_adapter_not_a_project_file():
    """The config never names a project's own graph file directly — only the
    adapter, which reads the actual target from `AEGRA_GRAPH_TARGET` at run
    time. See graph_adapter.py and README.md for why the adapter exists.
    """
    config = _load_template()
    graphs = config["graphs"]
    assert graphs == {"agent": "./graph_adapter.py:graph"}


def test_the_template_carries_no_langgraph_json_only_keys():
    """`checkpointer.path` is the specific example this scaffolding replaces —
    `aegra` overwrites whatever checkpointer a graph carries regardless, so a
    custom hook here would be silently ignored rather than erroring loudly.
    """
    config = _load_template()
    assert "checkpointer" not in config


def test_the_http_app_key_points_at_the_generic_adapter_not_a_project_file():
    """Same reasoning as the graph entry: the config only ever names the
    adapter (`http_app_adapter.py`), which reads the actual target from
    `AEGRA_HTTP_APP_TARGET` at run time. See http_app_adapter.py and README.md.
    """
    config = _load_template()
    assert config["http"] == {"app": "./http_app_adapter.py:app"}


def test_the_template_only_declares_keys_this_ticket_uses():
    config = _load_template()
    assert set(config.keys()) == {"dependencies", "graphs", "http"}


def test_requirements_pin_aegra_cli_to_an_exact_version():
    """Floating this would silently change graph-loader behavior; the pinned
    version is also what graph_adapter.py's workaround was verified against.
    """
    requirements = (AEGRA_HOST / "requirements.txt").read_text()
    pins = [
        line.strip()
        for line in requirements.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert pins == ["aegra-cli==0.10.5"]
