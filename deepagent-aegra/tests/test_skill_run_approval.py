"""A Skill Run pauses for an `mcp` write approval and resumes (issue #50, ADR-0011).

An approval is an ordinary LangGraph interrupt in the Run's thread, so the pause and
resume are the `mcp` subagent's own (tests/test_mcp_subagent.py); what is Skill Run
specific is the one-active-at-a-time slot: the paused Run gives it up, and the resumed
Run waits for it again. Same harness as the MCP tests: real graph, scripted model, a real
local MCP server.
"""
from __future__ import annotations

import asyncio

import pytest
from langgraph.types import Command

import fake_mcp_server
from agent import skill_run
from agent.skills import history, library
from tests.test_mcp_subagent import APPROVE, REJECT, _connect, _env, _reply, _Run  # noqa: F401  (_env is an autouse fixture)
from tests.test_skills_app import SKILL_MD, make_zip


@pytest.fixture(autouse=True)
def skills(tmp_path, monkeypatch):
    monkeypatch.setenv("SKILL_LIBRARY_DIR", str(tmp_path / "skills"))
    monkeypatch.setenv("FILE_STORE_DIR", str(tmp_path / "files"))
    monkeypatch.setattr(skill_run, "_GATE", skill_run.RunGate())
    library.install(make_zip({"SKILL.md": SKILL_MD}))
    _connect("github")


class _SkillRun(_Run):
    def _config(self, thread: str) -> dict:
        skill = {"name": "reconcile", "fields": {}, "files": {}}
        return {"configurable": {"thread_id": thread, "skill_run": skill}}


def test_a_write_pauses_the_run_and_approving_it_completes_the_run():
    run = _SkillRun(("github_write_thing",))

    first = run.start()

    assert len(first["__interrupt__"]) == 1  # the same approval request the chat shows
    assert fake_mcp_server.CALLS == []
    assert run.pending() and "__interrupt__" not in run.resume(APPROVE)
    assert fake_mcp_server.CALLS == ["write_thing"]


def test_a_run_paused_for_an_approval_stays_running_in_history_until_it_finishes():
    run = _SkillRun(("github_write_thing",))

    run.start()
    assert history.get("t1")["status"] == "running"  # paused, not failed

    run.resume(APPROVE)
    assert history.get("t1")["status"] == "done"


def test_rejecting_the_write_runs_nothing_and_the_run_still_ends():
    run = _SkillRun(("github_write_thing",))
    run.start()

    reply = _reply(run.resume(REJECT))

    assert fake_mcp_server.CALLS == []
    assert "User rejected the tool call" in reply


def test_a_paused_run_frees_the_slot_and_the_resumed_run_waits_for_it_again():
    async def scenario():
        run = _SkillRun(("github_write_thing",))
        config = run._config("t1")
        first = await run.agent.ainvoke({"messages": [("human", "go")]}, config=config)
        assert first["__interrupt__"]

        await asyncio.wait_for(skill_run._GATE.acquire("other", lambda: None), 1)  # free while paused
        events: list[str] = []

        async def resume():  # like the browser's resume: just the thread, no `skill_run` configurable
            plain = {"configurable": {"thread_id": "t1"}}
            async for e in run.agent.astream(Command(resume=APPROVE), config=plain, stream_mode="custom"):
                events.append(e["skill_run_status"])

        task = asyncio.create_task(resume())
        for _ in range(500):
            if events:
                break
            await asyncio.sleep(0.01)
        assert events == ["queued"] and fake_mcp_server.CALLS == []  # the write waits for the other Run

        skill_run._GATE.release("other")
        await asyncio.wait_for(task, 10)
        return events

    assert asyncio.run(scenario()) == ["queued", "running"]
    assert fake_mcp_server.CALLS == ["write_thing"]
