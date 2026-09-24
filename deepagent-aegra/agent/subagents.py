"""Subagent definitions: the orchestrator's `task`-tool delegates.

Mirrors `agent-runtime/agent/subagents.py`'s separation — subagent specs are
assembled here, while each subagent's own tools and prompt live in their own
module (`agent/output_writer.py`). `file-reader` and `web-search` are later
tickets (#17, #18) and are not registered yet.
"""
from __future__ import annotations

from deepagents.middleware.subagents import SubAgent

from agent.output_writer import build_output_writer_subagent


def build_subagents() -> list[SubAgent]:
    """The named subagents available to the orchestrator's `task` tool."""
    return [build_output_writer_subagent()]
