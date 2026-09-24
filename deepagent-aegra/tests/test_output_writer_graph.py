"""Graph-level: the orchestrator delegates to `output-writer`, driven end to
end by a `ScriptedChatModel` — no live Ollama, no mocked file store.

docs/adr/0005-testing-strategy-carryover-from-agent-runtime.md (point 4)
calls for exactly this style for `output-writer`'s tool tests: a real,
valid file (openable by its own format library) lands via the store when
the agent "produces" one, and malformed/empty write input never issues a
key. tests/test_output_writer.py covers the pure validation/serialization
seams in isolation; this file covers the wiring — `task` → the subagent →
the tool → the `outputs` state field → the file actually on disk.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from openpyxl import load_workbook

from agent.config import Settings
from agent.graph import build_agent
from agent.scripted_model import ScriptedChatModel

_SETTINGS = Settings(ollama_model="test-model", ollama_context_window=8192)


@pytest.fixture
def file_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # `store_output_bytes` reads `get_settings()` fresh from the environment
    # (agent/files/store.py) rather than the `settings=` object passed to
    # `build_agent` — same env conftest.py otherwise strips for every test.
    monkeypatch.setenv("OLLAMA_MODEL", _SETTINGS.ollama_model)
    monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", str(_SETTINGS.ollama_context_window))
    monkeypatch.setenv("FILE_STORE_DIR", str(tmp_path))
    return tmp_path


def _tool_calls_seen(messages: list) -> list[str]:
    return [call["name"] for m in messages if isinstance(m, AIMessage) for call in (m.tool_calls or [])]


def _delegate_to_output_writer(description: str):
    """Orchestrator turn: delegate once via `task`, then report on the subagent's reply."""

    def respond(messages, tools):
        if "task" not in _tool_calls_seen(messages):
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": description, "subagent_type": "output-writer"},
                        "id": "orch-1",
                    }
                ],
            )
        return AIMessage(content=str(messages[-1].content))

    return respond


def _run(tools_responder, orchestrator_description: str):
    def respond(messages, tools):
        if "task" in tools:
            return _delegate_to_output_writer(orchestrator_description)(messages, tools)
        return tools_responder(messages, tools)

    agent = build_agent(model=ScriptedChatModel(responder=respond), settings=_SETTINGS)
    return agent.invoke(
        {"messages": [HumanMessage(content="please produce a file")]},
        config={"configurable": {"thread_id": "t1"}},
    )


def test_a_well_formed_xlsx_lands_via_the_store(file_store: Path):
    def output_writer_turn(messages, tools):
        assert "write_xlsx" in tools
        if "write_xlsx" not in _tool_calls_seen(messages):
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_xlsx",
                        "args": {
                            "name": "demo-workbook",
                            "sheets": [
                                {
                                    "sheet_name": "Revenue",
                                    "headers": ["Region", "Q2"],
                                    "rows": [["EMEA", 100], ["AMER", 200]],
                                }
                            ],
                        },
                        "id": "ow-1",
                    }
                ],
            )
        return AIMessage(content="Wrote demo-workbook.xlsx and registered it as an Output.")

    result = _run(output_writer_turn, "Write a revenue workbook.")

    outputs = result["outputs"]
    assert outputs == [{"key": outputs[0]["key"], "filename": "demo-workbook.xlsx"}]

    workbook = load_workbook(file_store / outputs[0]["key"])
    assert workbook["Revenue"]["A1"].value == "Region"
    assert workbook["Revenue"]["A2"].value == "EMEA"


def test_malformed_write_input_never_issues_a_key(file_store: Path):
    def output_writer_turn(messages, tools):
        if "write_xlsx" not in _tool_calls_seen(messages):
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_xlsx",
                        "args": {
                            "name": "broken-workbook",
                            "sheets": [
                                {
                                    "sheet_name": "Revenue",
                                    "headers": ["Region", "Q2", "Q3"],
                                    "rows": [["EMEA", 100]],
                                }
                            ],
                        },
                        "id": "ow-1",
                    }
                ],
            )
        return AIMessage(content='Could not write "broken-workbook" via write_xlsx: mismatch.')

    result = _run(output_writer_turn, "Write a broken workbook.")

    assert result.get("outputs", []) == []
    assert list(file_store.iterdir()) == []


def test_empty_write_input_never_issues_a_key(file_store: Path):
    def output_writer_turn(messages, tools):
        if "write_txt" not in _tool_calls_seen(messages):
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_txt",
                        "args": {"name": "empty-notes", "text": "   "},
                        "id": "ow-1",
                    }
                ],
            )
        return AIMessage(content='Could not write "empty-notes" via write_txt: empty.')

    result = _run(output_writer_turn, "Write an empty notes file.")

    assert result.get("outputs", []) == []
    assert list(file_store.iterdir()) == []


def test_outputs_accumulates_across_multiple_writes_in_one_run(file_store: Path):
    """A run that produces more than one file — why `outputs` is a list."""

    def output_writer_turn(messages, tools):
        seen = _tool_calls_seen(messages)
        if "write_pptx" not in seen:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_pptx",
                        "args": {
                            "name": "board-deck",
                            "slides": [{"title": "Intro", "bullets": ["Point one"], "speaker_notes": ""}],
                        },
                        "id": "ow-1",
                    }
                ],
            )
        if "write_txt" not in seen:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_txt",
                        "args": {"name": "run-notes", "text": "Reminder: demo runs Ollama-only."},
                        "id": "ow-2",
                    }
                ],
            )
        return AIMessage(content="Wrote board-deck.pptx and run-notes.txt.")

    result = _run(output_writer_turn, "Write a deck and its notes as text.")

    filenames = {o["filename"] for o in result["outputs"]}
    assert filenames == {"board-deck.pptx", "run-notes.txt"}
    assert len(result["outputs"]) == 2
