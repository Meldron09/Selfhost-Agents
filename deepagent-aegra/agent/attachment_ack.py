"""`AttachmentAcknowledgeMiddleware`: makes `attachments` visible to the orchestrator.

`attachments` (agent/state.py) is a custom LangGraph state field, not part of
`state["messages"]` -- nothing puts it in front of the model unless something
does so explicitly. CONTEXT.md's Orchestrator definition names this exactly:
"responsible for acknowledging every received Attachment by name in its
first reply (the mitigation for LangGraph's silent-drop-on-unrecognized-
state-key behavior)". Without this middleware, an Attachment could sail
through the graph unacknowledged and the person would have no signal it was
ever received.

Implemented as `wrap_model_call` rather than a message inserted into
`state["messages"]`: it rewrites the system prompt fresh on every model call
from `request.state["attachments"]`, so it never accumulates duplicate notes
across a multi-turn conversation the way appending a message would, and it
only ever affects the orchestrator's own model calls -- passed via
`create_deep_agent(middleware=...)`, which (per deepagents/graph.py) is not
inherited by declarative `SubAgent`s like `file-reader`/`output-writer`,
each of which builds its own middleware stack from scratch.

`attachments` uses `operator.add` (agent/state.py), so it can hold entries
from earlier turns of the same thread, not only the current run -- the note
below says "available in this thread", not "received this run", so it stays
honest on turn two once the middleware itself can't tell which entries are
new. `agent/graph.py`'s SYSTEM_PROMPT is what keeps delegation from
repeating every turn: it tells the orchestrator to delegate an Attachment to
`file-reader` the first time it sees it, not unconditionally on every later
turn too. See docs/adr/0006 for why this split (mechanical visibility here,
re-delegation judgment in the prompt) was the chosen tradeoff.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage


def _with_attachment_note(request: ModelRequest) -> ModelRequest:
    attachments = request.state.get("attachments") or []
    if not attachments:
        return request

    lines = "\n".join(f'- "{a["filename"]}" (key: {a["key"]})' for a in attachments)
    note = f"Attachments available in this thread:\n{lines}"

    existing = request.system_message.text if request.system_message else ""
    combined = f"{existing}\n\n{note}" if existing else note
    return request.override(system_message=SystemMessage(content=combined))


class AttachmentAcknowledgeMiddleware(AgentMiddleware):
    """Surfaces `state["attachments"]` to the orchestrator via the system prompt."""

    name = "AttachmentAcknowledgeMiddleware"

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(_with_attachment_note(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        # `aegra serve` (and tests/test_server_compat.py's blockbuster guard)
        # invoke the graph asynchronously; the base `AgentMiddleware` only
        # provides `awrap_model_call` when a sync `wrap_model_call` is
        # otherwise unavailable, and its default raises `NotImplementedError`
        # rather than falling back to the sync path.
        return await handler(_with_attachment_note(request))
