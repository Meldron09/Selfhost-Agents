"""aegra-host/http_app_adapter.py must hand aegra a fresh app, and only when asked.

`aegra`'s custom-app loader requires the target to already be a `FastAPI`
instance (see test_aegra_host_http_mount.py for why), so a project whose app
module exposes a factory (the pattern agent/artifacts/app.py follows, and
which docs/DECISIONS.md's "Two instances from one factory" entry recommends)
needs this adapter to call it. Mirrors test_aegra_host_graph_adapter.py's
shape for the analogous graph-side adapter.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ADAPTER_PATH = REPO_ROOT / "aegra-host" / "http_app_adapter.py"


def _load_adapter() -> ModuleType:
    spec = importlib.util.spec_from_file_location("aegra_host_http_app_adapter", ADAPTER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_factory_target(tmp_path: Path) -> Path:
    target = tmp_path / "http.py"
    target.write_text(
        "class _FakeApp:\n"
        "    pass\n\n"
        "def create_app():\n"
        "    return _FakeApp()\n"
    )
    return target


@pytest.fixture
def fake_instance_target(tmp_path: Path) -> Path:
    """A real `FastAPI`/`Starlette` app instance is itself callable — the ASGI
    protocol is `__call__(self, scope, receive, send)` — so this fixture's
    `__call__` mimics that, guarding against treating an instance as a
    zero-arg factory to invoke (see `_build_app`'s `callable()` pitfall).
    """
    target = tmp_path / "http.py"
    target.write_text(
        "class _FakeApp:\n"
        "    def __call__(self, *args, **kwargs):\n"
        "        raise AssertionError('an app instance must never be called as a factory')\n\n"
        "app = _FakeApp()\n"
    )
    return target


def test_loading_a_factory_target_calls_it_for_a_fresh_instance(
    monkeypatch: pytest.MonkeyPatch, fake_factory_target: Path
):
    monkeypatch.setenv("AEGRA_HTTP_APP_DEPENDENCY_PATH", str(fake_factory_target.parent))
    monkeypatch.setenv("AEGRA_HTTP_APP_TARGET", f"{fake_factory_target.name}:create_app")

    first = _load_adapter().app
    second = _load_adapter().app

    assert type(first).__name__ == "_FakeApp"
    assert first is not second, "each load must call the factory again, not share one instance"


def test_loading_an_already_built_instance_target_passes_it_through(
    monkeypatch: pytest.MonkeyPatch, fake_instance_target: Path
):
    monkeypatch.setenv("AEGRA_HTTP_APP_DEPENDENCY_PATH", str(fake_instance_target.parent))
    monkeypatch.setenv("AEGRA_HTTP_APP_TARGET", f"{fake_instance_target.name}:app")

    adapter = _load_adapter()

    assert type(adapter.app).__name__ == "_FakeApp"


def test_the_dependency_path_is_importable_from_inside_the_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """The real failure this guards: `aegra.json`'s own `dependencies` entry
    only reaches `sys.path` lazily (inside `LangGraphService.__init__`, first
    triggered from the request lifespan) — too late for this adapter, which
    runs eagerly at `aegra_api.main` import time. A target module with an
    ordinary absolute import of a sibling module (`agent/artifacts/app.py`
    importing `agent.artifacts.store`, in the real project) would otherwise
    fail with `ModuleNotFoundError` even with the right path configured.
    """
    package_name = "aegra_host_adapter_test_sibling_pkg"
    package_dir = tmp_path / package_name
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("")
    (package_dir / "util.py").write_text("VALUE = 42\n")
    target = tmp_path / "http.py"
    target.write_text(f"from {package_name}.util import VALUE\n\ndef create_app():\n    return VALUE\n")
    monkeypatch.delitem(sys.modules, package_name, raising=False)
    monkeypatch.delitem(sys.modules, f"{package_name}.util", raising=False)

    monkeypatch.setenv("AEGRA_HTTP_APP_DEPENDENCY_PATH", str(tmp_path))
    monkeypatch.setenv("AEGRA_HTTP_APP_TARGET", "http.py:create_app")

    adapter = _load_adapter()

    assert adapter.app == 42


def test_a_missing_dependency_path_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AEGRA_HTTP_APP_DEPENDENCY_PATH", raising=False)
    monkeypatch.setenv("AEGRA_HTTP_APP_TARGET", "http.py:app")

    with pytest.raises(RuntimeError):
        _load_adapter()


def test_a_missing_target_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("AEGRA_HTTP_APP_DEPENDENCY_PATH", str(tmp_path))
    monkeypatch.delenv("AEGRA_HTTP_APP_TARGET", raising=False)

    with pytest.raises(RuntimeError):
        _load_adapter()


def test_a_malformed_target_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("AEGRA_HTTP_APP_DEPENDENCY_PATH", str(tmp_path))
    monkeypatch.setenv("AEGRA_HTTP_APP_TARGET", "no-colon-here")

    with pytest.raises(ValueError):
        _load_adapter()


def test_a_missing_target_file_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("AEGRA_HTTP_APP_DEPENDENCY_PATH", str(tmp_path))
    monkeypatch.setenv("AEGRA_HTTP_APP_TARGET", "does_not_exist.py:app")

    with pytest.raises(FileNotFoundError):
        _load_adapter()
