"""Skill Run history: what a Skill Run records and the `/skill-runs` routes (issue #51, ADR-0011).

The Run's own middleware writes the record (start, then the outcome), so these drive the real
graph with a scripted model, and read the history back through the HTTP routes.
"""
from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from starlette.testclient import TestClient

from agent.config import Settings
from agent.files import create_app
from agent.graph import build_agent
from agent.scripted_model import ScriptedChatModel
from agent.skills import history, library
from tests.test_skill_run_queue import Scenario, _cancelled
from tests.test_skills_app import SKILL_MD, make_zip

_SETTINGS = Settings(ollama_model="test-model", ollama_context_window=8192)


@pytest.fixture(autouse=True)
def stores(tmp_path, monkeypatch):
    monkeypatch.setenv("SKILL_LIBRARY_DIR", str(tmp_path / "skills"))
    monkeypatch.setenv("FILE_STORE_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", "8192")
    library.install(make_zip({"SKILL.md": SKILL_MD, "rules.md": "No refunds."}))


@pytest.fixture
def skills_dir_with_secret(tmp_path):
    (tmp_path / "skills" / "secret.json").write_text('{"skill": "x", "startedAt": "", "fingerprint": null}')


@pytest.fixture
def client():
    return TestClient(create_app())


def _run(thread: str, *, skill: str = "reconcile", fields=None, files=None, reply="All done."):
    agent = build_agent(model=ScriptedChatModel(responder=lambda m, t: AIMessage(content=reply)), settings=_SETTINGS)
    return agent.invoke(
        {"messages": [HumanMessage(content="go")]},
        config={
            "configurable": {
                "thread_id": thread,
                "skill_run": {"name": skill, "fields": fields or {}, "files": files or {}},
            }
        },
    )


# --- what a Run records --------------------------------------------------------


def test_a_finished_run_records_what_was_submitted_its_final_message_and_outputs(client):
    files = {"ledger": [{"key": "up1.xlsx", "filename": "q1.xlsx"}]}
    _run("t-1", fields={"period": "Q1"}, files=files, reply="Reconciled.")

    [run] = client.get("/skill-runs", params={"skill": "reconcile"}).json()

    assert run["id"] == "t-1" and run["skill"] == "reconcile"
    assert run["fields"] == {"period": "Q1"} and run["files"] == files
    assert run["status"] == "done" and run["finalMessage"] == "Reconciled."
    assert run["outputs"] == [] and run["skillState"] is None
    assert run["startedAt"] and run["installedAt"]
    assert client.get("/skill-runs/t-1").json() == run


def test_a_normal_chat_leaves_no_history(client):
    agent = build_agent(model=ScriptedChatModel(responder=lambda m, t: AIMessage(content="hi")), settings=_SETTINGS)
    agent.invoke({"messages": [HumanMessage(content="hi")]}, config={"configurable": {"thread_id": "chat"}})

    assert client.get("/skill-runs").json() == []


def test_a_failed_run_is_recorded_as_failed(client):
    async def scenario():
        s = Scenario(hold="nobody", fail="a")
        with pytest.raises(RuntimeError):
            await s.submit("a")

    asyncio.run(scenario())

    [run] = client.get("/skill-runs").json()
    assert run["id"] == "t-a" and run["status"] == "failed"


def test_a_cancelled_run_is_recorded_as_cancelled(client):
    async def scenario():
        s = Scenario(hold="a")
        a = asyncio.create_task(s.submit("a"))
        await s.active_is_in_its_model_call()
        await _cancelled(a)
        s.release_hold.set()

    asyncio.run(scenario())

    [run] = client.get("/skill-runs").json()
    assert run["id"] == "t-a" and run["status"] == "cancelled"


# --- list and detail -----------------------------------------------------------


def test_runs_are_listed_newest_first_and_filtered_by_skill(client):
    library.install(make_zip({"SKILL.md": SKILL_MD.replace("reconcile", "other")}))
    _run("t-1")
    _run("t-2", skill="other")
    _run("t-3")

    assert [r["id"] for r in client.get("/skill-runs").json()] == ["t-3", "t-2", "t-1"]
    assert [r["id"] for r in client.get("/skill-runs", params={"skill": "reconcile"}).json()] == ["t-3", "t-1"]


def test_an_unknown_run_is_a_404_naming_the_rule(client):
    res = client.get("/skill-runs/nope")
    assert res.status_code == 404 and "No Skill Run" in res.json()["error"]


def test_a_run_id_is_never_read_as_a_path(skills_dir_with_secret):
    assert history.get("../secret") is None


# --- the cap -------------------------------------------------------------------


def test_only_the_last_50_runs_of_a_skill_are_kept(client, monkeypatch):
    monkeypatch.setattr(history, "CAP", 3)
    library.install(make_zip({"SKILL.md": SKILL_MD.replace("reconcile", "other")}))
    _run("o-1", skill="other")
    for i in range(1, 5):
        _run(f"t-{i}")

    ids = [r["id"] for r in client.get("/skill-runs").json()]

    assert ids == ["t-4", "t-3", "t-2", "o-1"]  # t-1 trimmed; the other Skill keeps its own count
    assert client.get("/skill-runs/t-1").status_code == 404


def test_the_cap_is_50(client):
    assert history.CAP == 50


# --- removed and updated -------------------------------------------------------


def test_a_run_of_a_deleted_skill_is_marked_removed_and_still_readable(client):
    _run("t-1")
    library.delete("reconcile")

    assert client.get("/skill-runs/t-1").json()["skillState"] == "removed"
    assert [r["skillState"] for r in client.get("/skill-runs", params={"skill": "reconcile"}).json()] == ["removed"]


def test_a_run_of_a_replaced_skill_is_marked_updated_but_a_later_run_is_not(client):
    _run("t-1")
    library.replace("reconcile", make_zip({"SKILL.md": SKILL_MD, "rules.md": "Refunds within 30 days."}))
    _run("t-2")

    states = {r["id"]: r["skillState"] for r in client.get("/skill-runs").json()}

    assert states == {"t-1": "updated", "t-2": None}


def test_replacing_a_skill_with_identical_content_does_not_mark_its_runs_updated(client):
    _run("t-1")
    library.replace("reconcile", make_zip({"SKILL.md": SKILL_MD, "rules.md": "No refunds."}))

    assert client.get("/skill-runs/t-1").json()["skillState"] is None
