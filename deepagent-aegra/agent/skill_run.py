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

One Skill Run is active at a time (issue #48, ADR-0011): `abefore_agent` first waits its
turn at `_GATE`, a FIFO, emitting `{"skill_run_status": "queued"}` (only if it had to
wait) and then `"running"` as custom stream events. The slot is released when the Run
ends -- `aafter_agent`, or the exception that escapes a model or tool call (failure,
cancellation, or an approval interrupt pausing the Run). A normal chat never touches it.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage
from langgraph.config import get_config, get_stream_writer

from agent.files.store import store_output_bytes
from agent.skills import library


class RunGate:
    """One holder at a time; the rest wait in arrival order. Release hands the slot
    straight to the next waiter, so `_holder` is never empty while anyone waits.
    A holder that never reports back (a failure no hook sees) loses the slot after
    `lease_secs`, aegra's own per-run timeout, so one stuck Run cannot block the queue for good.
    ponytail: in-process, so it assumes one server process (aegra with the Redis broker
    off, its default); a multi-worker deployment needs a shared lock instead.
    """

    def __init__(self, lease_secs: float = 3600) -> None:
        self._lease_secs = lease_secs
        self._lease: asyncio.TimerHandle | None = None
        self._holder: str | None = None
        self._waiting: deque[tuple[str, asyncio.Future[None]]] = deque()

    async def acquire(self, run_id: str, on_queued: Callable[[], None]) -> None:
        if self._holder in (None, run_id):
            self._grant(run_id)
            return
        entry = (run_id, asyncio.get_running_loop().create_future())
        self._waiting.append(entry)
        on_queued()
        try:
            await entry[1]
        except BaseException:
            if entry[1].done() and not entry[1].cancelled():  # granted as we were cancelled
                self.release(run_id)
            else:
                with contextlib.suppress(ValueError):
                    self._waiting.remove(entry)
            raise

    def _grant(self, run_id: str) -> None:
        self._holder = run_id
        if self._lease:
            self._lease.cancel()
        self._lease = asyncio.get_running_loop().call_later(self._lease_secs, self.release, run_id)

    def release(self, run_id: str) -> None:
        """A no-op unless `run_id` holds the slot, so every exit path may call it."""
        if self._holder != run_id:
            return
        self._holder = None
        if self._lease:
            self._lease.cancel()
        while self._waiting:
            next_id, future = self._waiting.popleft()
            if not future.done():
                self._grant(next_id)
                future.set_result(None)
                return


_GATE = RunGate()


def _run_id() -> str:
    return str((get_config().get("configurable") or {}).get("thread_id"))


def _render(name: str, fields: dict, uploads: dict[str, list[dict]], references: list[dict]) -> str:
    parts = [
        f'## Skill Run: "{name}"',
        "This is a one-shot Skill Run: follow the Skill's instructions below using the inputs, then "
        "reply with a final message. Nobody is available for follow-up questions.",
        "### Skill instructions",
        library.instructions(name),
        "### Inputs (fields)",
        "```json\n" + json.dumps(fields, indent=2, ensure_ascii=False) + "\n```",
    ]
    uploaded = [(field, a) for field, files in uploads.items() for a in files]
    if uploaded:
        lines = "\n".join(f'- field "{f}": "{a["filename"]}" (key: {a["key"]})' for f, a in uploaded)
        parts += ["### Uploaded files", lines]
    if references:
        lines = "\n".join(f'- "{a["filename"]}" (key: {a["key"]})' for a in references)
        parts += [
            "### Skill reference files",
            "These are the exception to reading every Attachment up front: do not delegate them to "
            "`file-reader` until the instructions need one.",
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
    """Injects a Skill Run's instructions, fields and files into the Orchestrator's prompt,
    and holds the Run's turn in the one-active-at-a-time queue."""

    name = "SkillRunMiddleware"

    def before_agent(self, state, runtime) -> dict[str, Any] | None:
        skill_run = _skill_run(state)
        return _prepare(skill_run) if skill_run else None

    async def abefore_agent(self, state, runtime) -> dict[str, Any] | None:
        skill_run = _skill_run(state)
        if not skill_run:
            return None
        run_id, write = _run_id(), get_stream_writer()
        await _GATE.acquire(run_id, lambda: write({"skill_run_status": "queued"}))
        try:
            write({"skill_run_status": "running"})
            return await asyncio.to_thread(_prepare, skill_run)
        except BaseException:
            _GATE.release(run_id)
            raise

    # Only the async path acquires the gate: the sync one is the standalone runner, one Run at a
    # time. The sync hooks below exist because langchain refuses a sync run with only an async twin.
    def after_agent(self, state, runtime) -> None:
        _GATE.release(_run_id())

    async def aafter_agent(self, state, runtime) -> None:
        _GATE.release(_run_id())

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
        try:
            return await handler(_with_context(request))
        except BaseException:
            _GATE.release(_run_id())
            raise

    def wrap_tool_call(self, request, handler):
        try:
            return handler(request)
        except BaseException:
            _GATE.release(_run_id())
            raise

    async def awrap_tool_call(self, request, handler):
        try:
            return await handler(request)
        except BaseException:
            _GATE.release(_run_id())
            raise
