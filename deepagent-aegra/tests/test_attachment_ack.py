"""`agent.attachment_ack`'s pure request-rewriting seam.

`AttachmentAcknowledgeMiddleware.wrap_model_call` delegates to
`_with_attachment_note`, which is exercised directly here with no model or
graph involved -- the graph-level effect (the orchestrator's first reply
actually naming every Attachment, and delegating each one to `file-reader`)
is exercised end to end in tests/test_graph.py, driven by a
`ScriptedChatModel` that inspects the rewritten system message.
"""
from __future__ import annotations

from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import HumanMessage, SystemMessage

from agent.attachment_ack import _with_attachment_note


def _request(*, attachments=None, system_message=None) -> ModelRequest:
    return ModelRequest(
        model=None,
        messages=[HumanMessage(content="hi")],
        system_message=system_message,
        state={"messages": [], "attachments": attachments or []},
    )


def test_no_attachments_leaves_the_system_message_untouched():
    original = SystemMessage(content="You are the orchestrator.")
    request = _request(system_message=original)

    rewritten = _with_attachment_note(request)

    assert rewritten.system_message is original


def test_attachments_are_appended_to_an_existing_system_message_by_filename():
    original = SystemMessage(content="You are the orchestrator.")
    request = _request(
        system_message=original,
        attachments=[{"key": "a1.xlsx", "filename": "revenue.xlsx"}],
    )

    rewritten = _with_attachment_note(request)

    assert "You are the orchestrator." in rewritten.system_message.text
    assert "revenue.xlsx" in rewritten.system_message.text
    assert "a1.xlsx" in rewritten.system_message.text


def test_attachments_with_no_system_message_still_produce_a_note():
    request = _request(system_message=None, attachments=[{"key": "a1.pdf", "filename": "report.pdf"}])

    rewritten = _with_attachment_note(request)

    assert "report.pdf" in rewritten.system_message.text


def test_multiple_attachments_are_all_named():
    request = _request(
        attachments=[
            {"key": "a1.xlsx", "filename": "revenue.xlsx"},
            {"key": "a2.pdf", "filename": "report.pdf"},
        ]
    )

    rewritten = _with_attachment_note(request)

    text = rewritten.system_message.text
    assert "revenue.xlsx" in text
    assert "report.pdf" in text
