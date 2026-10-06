"""`agent.skill_run.SkillRunMiddleware`, wired into the real graph (issue #45, ADR-0011).

A Skill Run is an ordinary Orchestrator thread; the host only sends
`configurable.skill_run = {name, fields, files}`. These tests drive `build_agent`
with a `ScriptedChatModel` that records what the Orchestrator's model call
actually receives -- no live Ollama.
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.config import Settings
from agent.files.store import load
from agent.graph import build_agent
from agent.scripted_model import ScriptedChatModel
from agent.skills import library
from tests.test_skills_app import SKILL_MD, make_zip

_SETTINGS = Settings(ollama_model="test-model", ollama_context_window=8192)


@pytest.fixture
def stores(tmp_path, monkeypatch):
    monkeypatch.setenv("SKILL_LIBRARY_DIR", str(tmp_path / "skills"))
    monkeypatch.setenv("FILE_STORE_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")  # `get_settings()` fails loud without these
    monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", "8192")
    return tmp_path / "files"


def _install_skill():
    library.install(make_zip({"SKILL.md": SKILL_MD, "ui/index.html": "<p>hi</p>", "rules/policy.md": "No refunds."}))


def _run(configurable: dict):
    seen: list[str] = []

    def responder(messages, tools):
        seen.append("\n".join(str(m.content) for m in messages))
        return AIMessage(content="All done.")

    agent = build_agent(model=ScriptedChatModel(responder=responder), settings=_SETTINGS)
    result = agent.invoke(
        {"messages": [HumanMessage(content='Run the Skill "reconcile".')]},
        config={"configurable": {"thread_id": "t-skill", **configurable}},
    )
    return seen[0], result


def test_the_model_call_receives_instructions_fields_uploads_and_reference_files(stores):
    _install_skill()
    prompt, result = _run(
        {
            "skill_run": {
                "name": "reconcile",
                "fields": {"text": " Compare Q1 – café "},
                "files": {"files": [{"key": "up1.xlsx", "filename": "q1.xlsx"}]},
            }
        }
    )

    assert "Compare them." in prompt  # the SKILL.md body ...
    assert "description: Reconcile" not in prompt  # ... without its frontmatter
    assert '"text": " Compare Q1 – café "' in prompt  # fields, verbatim, as a JSON block
    assert '"q1.xlsx" (key: up1.xlsx)' in prompt and 'field "files"' in prompt  # upload, by field name
    assert "Skill reference files" in prompt and "rules/policy.md" in prompt
    assert "not delegate them to `file-reader`" in prompt  # reference files are read on demand
    assert "ui/index.html" not in prompt  # the Skill UI is not a reference file
    assert result["messages"][-1].content == "All done."


def test_reference_files_are_registered_as_attachments_the_file_store_can_resolve(stores):
    _install_skill()
    _, result = _run({"skill_run": {"name": "reconcile", "fields": {}, "files": {}}})

    refs = [a for a in result["attachments"] if a["filename"] == "rules/policy.md"]
    assert len(refs) == 1
    assert load(stores, refs[0]["key"]) == b"No refunds."


def test_uploaded_files_are_registered_as_attachments_too(stores):
    _install_skill()
    _, result = _run(
        {"skill_run": {"name": "reconcile", "fields": {}, "files": {"files": [{"key": "up1.xlsx", "filename": "q1.xlsx"}]}}}
    )

    assert {"key": "up1.xlsx", "filename": "q1.xlsx"} in result["attachments"]


def test_a_run_with_no_skill_run_config_is_untouched(stores):
    prompt, result = _run({})

    assert "Skill Run" not in prompt
    assert not result.get("attachments")


def test_a_run_of_a_skill_that_is_not_installed_fails(stores):
    with pytest.raises(library.SkillError, match="No Skill named 'gone'"):
        _run({"skill_run": {"name": "gone", "fields": {}, "files": {}}})
