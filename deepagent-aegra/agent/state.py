"""Custom graph state: `outputs`, the pointer-shaped list of files
`output-writer` has produced this run, and `attachments`, the pointer-shaped
list of files the person uploaded this run.

docs/adr/0001-pointer-shaped-file-references-via-post-run-state-fetch.md is
the design this implements: `{key, filename}` pairs, not inline content, so
`agent-chat-ui` discovers a finished file through a post-run state fetch
rather than parsing the orchestrator's chat reply. A list, not a single
field, because a run can carry more than one Output or Attachment
(docs/adr/0001).
"""
from __future__ import annotations

import operator
from typing import Annotated, NotRequired, TypedDict

from deepagents.graph import DeepAgentState


class OutputRef(TypedDict):
    key: str
    filename: str


class AttachmentRef(TypedDict):
    key: str
    filename: str


class DeepAgentAegraState(DeepAgentState):
    """`DeepAgentState` plus `outputs` and `attachments`.

    `operator.add` accumulates entries across multiple `output-writer` tool
    calls in the same run, or multiple attachment uploads across a thread's
    turns (list concatenation), rather than the last write overwriting the
    field.
    """

    outputs: NotRequired[Annotated[list[OutputRef], operator.add]]
    attachments: NotRequired[Annotated[list[AttachmentRef], operator.add]]
