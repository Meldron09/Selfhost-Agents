"""Generic factory-shape adapter for aegra's graph loader.

`aegra`'s loader treats any callable graph export as an async factory and
unconditionally awaits its result — a stricter contract than LangGraph
Server's, which also accepts a plain synchronous factory. This adapter
normalizes sync-factory, async-factory, and already-compiled-graph exports
into the one shape aegra expects, so the hosted project's own graph module
needs no change. See README.md.

Configured entirely through environment variables, so this file carries no
reference to any specific project:

  AEGRA_GRAPH_DEPENDENCY_PATH  - project source root (also on sys.path via
                                  aegra.json's `dependencies` entry)
  AEGRA_GRAPH_TARGET           - "<file relative to that root>:<export name>"
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import os
from pathlib import Path
from typing import Any


def _load_export() -> Any:
    dependency_path = os.environ.get("AEGRA_GRAPH_DEPENDENCY_PATH")
    if not dependency_path:
        raise RuntimeError("AEGRA_GRAPH_DEPENDENCY_PATH is not set")

    target = os.environ.get("AEGRA_GRAPH_TARGET")
    if not target:
        raise RuntimeError("AEGRA_GRAPH_TARGET is not set")

    file_part, sep, attr = target.partition(":")
    if not sep or not attr:
        raise ValueError(f"AEGRA_GRAPH_TARGET must be '<file>:<export>', got {target!r}")

    file_path = (Path(dependency_path) / file_part).resolve()
    if not file_path.exists():
        raise FileNotFoundError(f"AEGRA_GRAPH_TARGET file not found: {file_path}")

    spec = importlib.util.spec_from_file_location("aegra_host._graph_target", file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load graph target from {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, attr):
        raise AttributeError(f"{file_path} has no attribute {attr!r}")
    return getattr(module, attr)


async def graph() -> Any:
    """The export `aegra.json` points at: `./graph_adapter.py:graph`.

    `_load_export` does blocking file I/O (path resolution, `exec_module`), and
    a synchronous factory may do blocking work of its own — both run in a
    thread rather than on the event loop this coroutine is awaited from,
    mirroring `langgraph_api.graph`'s own `run_in_executor` around graph
    loading. An async factory is awaited directly, on the assumption that
    whoever wrote it as `async def` already made it loop-safe.
    """
    export = await asyncio.to_thread(_load_export)
    if not callable(export):
        return export
    if inspect.iscoroutinefunction(export):
        return await export()
    return await asyncio.to_thread(export)
