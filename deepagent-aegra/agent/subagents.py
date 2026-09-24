"""Subagent definitions: the orchestrator's `task`-tool delegates.

Mirrors `agent-runtime/agent/subagents.py`'s separation — subagent specs are
assembled here, while each subagent's own tools and prompt live in their own
module (`agent/output_writer.py`, `agent/file_reader.py`, `agent/web_search.py`).

All three register structurally, always — per-run `web-search` availability
is a runtime config check (`agent.web_search_gate.WebSearchGateMiddleware`),
not a build-time decision about the roster (issue #18's own acceptance
criterion).
"""
from __future__ import annotations

from deepagents.middleware.subagents import SubAgent

from agent.file_reader import build_file_reader_subagent
from agent.output_writer import build_output_writer_subagent
from agent.web_search import build_web_search_subagent


def build_subagents() -> list[SubAgent]:
    """The named subagents available to the orchestrator's `task` tool."""
    return [
        build_output_writer_subagent(),
        build_file_reader_subagent(),
        build_web_search_subagent(),
    ]
