"""Graph-level: the orchestrator delegates to `file-reader`, driven end to
end by a `ScriptedChatModel` — no live Ollama, no mocked file store.

docs/adr/0005-testing-strategy-carryover-from-agent-runtime.md (point 4)
calls for the same style test_output_writer_graph.py already uses for
`output-writer`: a real attachment lands on disk via the store, and the
scripted subagent turn calls the real tool against it. This file covers the
wiring — `task` → `file-reader` → a `read_*` tool → the file store → real
bytes actually parsed — plus the two failure shapes issue #17 calls out: an
unsupported extension short-circuits before any tool call, and a supported
extension with unparseable content gets an explicit tool-level error.
tests/test_file_reader.py covers the pure extraction seams in isolation.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
import xlwt
from langchain_core.messages import AIMessage, HumanMessage

from agent.config import Settings
from agent.files.store import save
from agent.graph import build_agent
from agent.output_writer import _serialize_docx, _serialize_pptx, _serialize_xlsx
from agent.scripted_model import ScriptedChatModel

_SETTINGS = Settings(ollama_model="test-model", ollama_context_window=8192)


def _legacy_xls(sheet_name: str, headers: list[str], rows: list[list[object]]) -> bytes:
    """A real legacy (OLE2/BIFF) .xls workbook -- see tests/test_file_reader.py's
    own copy of this helper for why `xlwt` (test-only) stands in for a fixture.
    """
    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet(sheet_name)
    for col, header in enumerate(headers):
        sheet.write(0, col, header)
    for row_index, row in enumerate(rows, start=1):
        for col, value in enumerate(row):
            sheet.write(row_index, col, value)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def file_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # `resolve_attachment_bytes` reads `get_settings()` fresh from the
    # environment (agent/files/store.py), same as test_output_writer_graph.py.
    monkeypatch.setenv("OLLAMA_MODEL", _SETTINGS.ollama_model)
    monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", str(_SETTINGS.ollama_context_window))
    monkeypatch.setenv("FILE_STORE_DIR", str(tmp_path))
    return tmp_path


def _tool_calls_seen(messages: list) -> list[str]:
    return [call["name"] for m in messages if isinstance(m, AIMessage) for call in (m.tool_calls or [])]


def _run(subagent_turn, attachments: list[dict], question: str):
    def orchestrator_turn(messages, tools):
        if "task" not in _tool_calls_seen(messages):
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": question, "subagent_type": "file-reader"},
                        "id": "orch-1",
                    }
                ],
            )
        return AIMessage(content=str(messages[-1].content))

    def responder(messages, tools):
        if "task" in tools:
            return orchestrator_turn(messages, tools)
        return subagent_turn(messages, tools)

    agent = build_agent(model=ScriptedChatModel(responder=responder), settings=_SETTINGS)
    return agent.invoke(
        {"messages": [HumanMessage(content=question)], "attachments": attachments},
        config={"configurable": {"thread_id": "t1"}},
    )


# --- round trips (docs/adr/0005, point 2: no static fixture for these four) ---


def test_read_xlsx_round_trips_a_real_workbook_through_the_store(file_store: Path):
    data = _serialize_xlsx(
        [{"sheet_name": "Revenue", "headers": ["Region", "Q2"], "rows": [["EMEA", 100], ["AMER", 200]]}]
    )
    key = save(file_store, "revenue.xlsx", data)

    def subagent_turn(messages, tools):
        assert "read_xlsx" in tools
        if "read_xlsx" not in _tool_calls_seen(messages):
            return AIMessage(content="", tool_calls=[{"name": "read_xlsx", "args": {"key": key}, "id": "fr-1"}])
        tool_message = messages[-1]
        assert tool_message.status == "success"
        assert "EMEA" in tool_message.content
        return AIMessage(content=f"revenue.xlsx contains: {tool_message.content}")

    result = _run(subagent_turn, [{"key": key, "filename": "revenue.xlsx"}], "what's in revenue.xlsx?")

    assert "EMEA" in result["messages"][-1].content


def test_read_xlsx_also_reads_a_real_legacy_xls_workbook_through_the_store(file_store: Path):
    """Issue #17: "read_xlsx (+.xls)" -- the same tool, a different on-disk
    format `openpyxl` alone can't parse (agent/file_reader.py's xlrd fallback).
    """
    data = _legacy_xls("Revenue", ["Region", "Q2"], [["EMEA", 100], ["AMER", 200]])
    key = save(file_store, "revenue.xls", data)

    def subagent_turn(messages, tools):
        if "read_xlsx" not in _tool_calls_seen(messages):
            return AIMessage(content="", tool_calls=[{"name": "read_xlsx", "args": {"key": key}, "id": "fr-1"}])
        tool_message = messages[-1]
        assert tool_message.status == "success"
        assert "EMEA" in tool_message.content
        return AIMessage(content=f"revenue.xls contains: {tool_message.content}")

    result = _run(subagent_turn, [{"key": key, "filename": "revenue.xls"}], "what's in revenue.xls?")

    assert "EMEA" in result["messages"][-1].content


def test_read_docx_round_trips_a_real_document_through_the_store(file_store: Path):
    data = _serialize_docx("Kickoff Memo\n\nScope: independent demo.")
    key = save(file_store, "memo.docx", data)

    def subagent_turn(messages, tools):
        if "read_docx" not in _tool_calls_seen(messages):
            return AIMessage(content="", tool_calls=[{"name": "read_docx", "args": {"key": key}, "id": "fr-1"}])
        tool_message = messages[-1]
        assert tool_message.status == "success"
        assert "Kickoff Memo" in tool_message.content
        return AIMessage(content=f"memo.docx says: {tool_message.content}")

    result = _run(subagent_turn, [{"key": key, "filename": "memo.docx"}], "summarize memo.docx")

    assert "Kickoff Memo" in result["messages"][-1].content


def test_read_pptx_round_trips_a_real_presentation_through_the_store(file_store: Path):
    data = _serialize_pptx([{"title": "Intro", "bullets": ["Point one"], "speaker_notes": ""}])
    key = save(file_store, "deck.pptx", data)

    def subagent_turn(messages, tools):
        if "read_pptx" not in _tool_calls_seen(messages):
            return AIMessage(content="", tool_calls=[{"name": "read_pptx", "args": {"key": key}, "id": "fr-1"}])
        tool_message = messages[-1]
        assert tool_message.status == "success"
        assert "Point one" in tool_message.content
        return AIMessage(content=f"deck.pptx says: {tool_message.content}")

    result = _run(subagent_turn, [{"key": key, "filename": "deck.pptx"}], "what's on the first slide?")

    assert "Point one" in result["messages"][-1].content


def test_read_txt_round_trips_real_bytes_through_the_store(file_store: Path):
    key = save(file_store, "notes.txt", b"Reminder: demo runs Ollama-only.")

    def subagent_turn(messages, tools):
        if "read_txt" not in _tool_calls_seen(messages):
            return AIMessage(content="", tool_calls=[{"name": "read_txt", "args": {"key": key}, "id": "fr-1"}])
        tool_message = messages[-1]
        assert tool_message.status == "success"
        return AIMessage(content=f"notes.txt says: {tool_message.content}")

    result = _run(subagent_turn, [{"key": key, "filename": "notes.txt"}], "what do the notes say?")

    assert "Ollama-only" in result["messages"][-1].content


def test_read_pdf_extracts_the_static_fixtures_embedded_text(file_store: Path):
    repo_root = Path(__file__).resolve().parent.parent
    sample_pdf = (repo_root / "tests" / "fixtures" / "sample.pdf").read_bytes()
    key = save(file_store, "report.pdf", sample_pdf)

    def subagent_turn(messages, tools):
        if "read_pdf" not in _tool_calls_seen(messages):
            return AIMessage(content="", tool_calls=[{"name": "read_pdf", "args": {"key": key}, "id": "fr-1"}])
        tool_message = messages[-1]
        assert tool_message.status == "success"
        assert "PYPDF_FIXTURE_MARKER_98214" in tool_message.content
        return AIMessage(content=f"report.pdf says: {tool_message.content}")

    result = _run(subagent_turn, [{"key": key, "filename": "report.pdf"}], "what's in report.pdf?")

    assert "PYPDF_FIXTURE_MARKER_98214" in result["messages"][-1].content


# --- failure shapes (acceptance criteria: no exception, no fabricated content) --


def test_a_supported_extension_with_corrupted_content_gets_an_explicit_tool_error(file_store: Path):
    key = save(file_store, "broken.xlsx", b"not actually a workbook")

    def subagent_turn(messages, tools):
        if "read_xlsx" not in _tool_calls_seen(messages):
            return AIMessage(content="", tool_calls=[{"name": "read_xlsx", "args": {"key": key}, "id": "fr-1"}])
        tool_message = messages[-1]
        assert tool_message.status == "error"
        return AIMessage(content=f'Could not read "broken.xlsx": {tool_message.content}')

    result = _run(subagent_turn, [{"key": key, "filename": "broken.xlsx"}], "what's in broken.xlsx?")

    assert "Could not read" in result["messages"][-1].content
    assert "broken.xlsx" in result["messages"][-1].content


def test_an_unknown_key_gets_an_explicit_tool_error_never_a_raised_exception(file_store: Path):
    def subagent_turn(messages, tools):
        if "read_txt" not in _tool_calls_seen(messages):
            return AIMessage(
                content="", tool_calls=[{"name": "read_txt", "args": {"key": "missing.txt"}, "id": "fr-1"}]
            )
        tool_message = messages[-1]
        assert tool_message.status == "error"
        return AIMessage(content=f"Could not read the file: {tool_message.content}")

    result = _run(subagent_turn, [{"key": "missing.txt", "filename": "notes.txt"}], "what do the notes say?")

    assert "Could not read" in result["messages"][-1].content


def test_an_unsupported_extension_short_circuits_before_any_tool_call(file_store: Path):
    def subagent_turn(messages, tools):
        # file-reader's own judgment call: no read_* tool exists for this
        # extension, so it must reply directly, naming the file, without
        # ever calling a tool.
        return AIMessage(content='The file type of "archive.zip" is unsupported.')

    result = _run(subagent_turn, [{"key": "k1.zip", "filename": "archive.zip"}], "what's in archive.zip?")

    assert "unsupported" in result["messages"][-1].content.lower()
    assert "archive.zip" in result["messages"][-1].content
    assert "read_pdf" not in _tool_calls_seen(result["messages"])
    assert "read_xlsx" not in _tool_calls_seen(result["messages"])


# --- demoable: a real question about a real attachment gets a real answer ---


def test_a_real_question_about_a_real_attachment_gets_an_answer_reflecting_its_actual_content(
    file_store: Path,
):
    data = _serialize_xlsx(
        [{"sheet_name": "Revenue", "headers": ["Region", "Q2"], "rows": [["EMEA", 1_450_000], ["AMER", 980_000]]}]
    )
    key = save(file_store, "quarterly-report.xlsx", data)

    def subagent_turn(messages, tools):
        if "read_xlsx" not in _tool_calls_seen(messages):
            return AIMessage(content="", tool_calls=[{"name": "read_xlsx", "args": {"key": key}, "id": "fr-1"}])
        tool_message = messages[-1]
        assert tool_message.status == "success"
        # A real answer, computed from the tool's actual JSON return value --
        # not a hardcoded number standing in for it.
        sheets = json.loads(tool_message.content)
        total = sum(row[1] for row in sheets[0]["rows"])
        return AIMessage(content=f"EMEA + AMER Q2 revenue is {total}.")

    result = _run(
        subagent_turn,
        [{"key": key, "filename": "quarterly-report.xlsx"}],
        "what's the combined EMEA and AMER Q2 revenue in quarterly-report.xlsx?",
    )

    assert "2430000" in result["messages"][-1].content.replace(",", "")
