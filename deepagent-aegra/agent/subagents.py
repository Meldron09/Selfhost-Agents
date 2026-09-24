"""Subagent definitions: the orchestrator's `task`-tool delegates.

Mirrors `agent-runtime/agent/subagents.py`'s separation — subagent specs are
assembled here, while each subagent's own tools and prompt live in their own
module (`agent/output_writer.py`, `agent/file_reader.py`). `web-search` is a
later ticket (#18) and is not registered yet.
"""
from __future__ import annotations

from deepagents.middleware.subagents import SubAgent

from agent.file_reader import build_file_reader_subagent
from agent.output_writer import build_output_writer_subagent


def build_subagents() -> list[SubAgent]:
    """The named subagents available to the orchestrator's `task` tool."""
    return [build_output_writer_subagent(), build_file_reader_subagent()]
