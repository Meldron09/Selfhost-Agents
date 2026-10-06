"""`SkillRunMiddleware`: gives the Orchestrator a Skill Run's context (issue #45, ADR-0011).

A Skill Run is an ordinary thread on the Orchestrator; the host marks it with
`configurable.skill_run = {name, fields, files}` (`files` maps a field name to the
uploaded `{key, filename}` Attachments). Two seams:

* `before_agent` runs once per invocation. It reads the Skill from the Skill
  Library, copies its reference files into the file store (so `file-reader` can
  resolve them by Key like any Attachment), registers uploaded and reference
  files in `attachments`, and renders the whole context into `skill_run_context`.
  All the disk work lives here -- the async variant runs it in a thread -- because
  the server forbids blocking calls inside a model call
  (tests/test_server_compat.py).
* `wrap_model_call` only appends that rendered text to the system prompt, the same
  shape as `AttachmentAcknowledgeMiddleware`.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage
from langgraph.config import get_config

from agent.files.store import store_output_bytes
from agent.skills import library


def _render(name: str, fields: dict, uploads: dict[str, list[dict]], references: list[dict]) -> str:
    parts = [
        f'## Skill Run: "{name}"',
        "This is a one-shot Skill Run: follow the Skill's instructions below using the inputs, then "
        "reply with a final message. Nobody is available for follow-up questions.",
        "### Skill instructions",
        library.instructions(name),
        "### Inputs (fields)",
        "```json\n" + json.dumps(fields, indent=2) + "\n```",
    ]
    uploaded = [(field, a) for field, files in uploads.items() for a in files]
    if uploaded:
        lines = "\n".join(f'- field "{f}": "{a["filename"]}" (key: {a["key"]})' for f, a in uploaded)
        parts += ["### Uploaded files", lines]
    if references:
        lines = "\n".join(f'- "{a["filename"]}" (key: {a["key"]})' for a in references)
        parts += [
            "### Skill reference files",
            "Read these through `file-reader` only when the instructions need them.",
            lines,
        ]
    return "\n\n".join(parts)


def _prepare(skill_run: dict[str, Any]) -> dict[str, Any]:
    name = skill_run["name"]
    uploads = skill_run.get("files") or {}
    references = [
        {"key": store_output_bytes(rel, data), "filename": rel}
        for rel, data in library.reference_files(name).items()
    ]
    return {
        "attachments": [a for files in uploads.values() for a in files] + references,
        "skill_run_context": _render(name, skill_run.get("fields") or {}, uploads, references),
    }


def _skill_run(state: dict) -> dict[str, Any] | None:
    if state.get("skill_run_context"):  # already prepared; never copy the files twice
        return None
    configurable = (get_config().get("configurable") or {})
    return configurable.get("skill_run") or None


def _with_context(request: ModelRequest) -> ModelRequest:
    context = request.state.get("skill_run_context")
    if not context:
        return request
    existing = request.system_message.text if request.system_message else ""
    combined = f"{existing}\n\n{context}" if existing else context
    return request.override(system_message=SystemMessage(content=combined))


class SkillRunMiddleware(AgentMiddleware):
    """Injects a Skill Run's instructions, fields and files into the Orchestrator's prompt."""

    name = "SkillRunMiddleware"

    def before_agent(self, state, runtime) -> dict[str, Any] | None:
        skill_run = _skill_run(state)
        return _prepare(skill_run) if skill_run else None

    async def abefore_agent(self, state, runtime) -> dict[str, Any] | None:
        skill_run = _skill_run(state)
        return await asyncio.to_thread(_prepare, skill_run) if skill_run else None

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(_with_context(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(_with_context(request))
