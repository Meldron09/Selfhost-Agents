"""Skill Runs queue: one active at a time, the rest wait in order (issue #48, ADR-0011).

Driven through the real graph with a `ScriptedChatModel` whose responder can be held
mid-call, so a Run is visibly "active" while others are submitted. What the person
sees is the `skill_run_status` custom stream event (`queued`, then `running`).
"""
from __future__ import annotations

import asyncio
import threading
from collections import defaultdict

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent import skill_run
from agent.config import Settings
from agent.graph import build_agent
from agent.scripted_model import ScriptedChatModel
from agent.skills import library
from tests.test_skills_app import SKILL_MD, make_zip

_SETTINGS = Settings(ollama_model="test-model", ollama_context_window=8192)


@pytest.fixture(autouse=True)
def stores(tmp_path, monkeypatch):
    monkeypatch.setenv("SKILL_LIBRARY_DIR", str(tmp_path / "skills"))
    monkeypatch.setenv("FILE_STORE_DIR", str(tmp_path / "files"))
    monkeypatch.setenv("OLLAMA_MODEL", "test-model")
    monkeypatch.setenv("OLLAMA_CONTEXT_WINDOW", "8192")
    library.install(make_zip({"SKILL.md": SKILL_MD}))


class Scenario:
    """Runs named by `fields.who`; the model call of `hold` blocks until `release_hold`."""

    def __init__(self, hold: str, fail: str | None = None):
        self.hold, self.fail = hold, fail
        self.release_hold = threading.Event()
        self.model_calls: list[str] = []  # who, in the order their model call started
        self.events: defaultdict[str, list[str]] = defaultdict(list)  # who -> skill_run_status states, in order
        self._in_hold_call = threading.Event()
        self.agent = build_agent(model=ScriptedChatModel(responder=self._respond), settings=_SETTINGS)

    def _respond(self, messages, tools):
        prompt = "\n".join(str(m.content) for m in messages)
        who = next((w for w in ("a", "b", "c") if f'"who": "{w}"' in prompt), "chat")
        self.model_calls.append(who)
        if who == self.hold:
            self._in_hold_call.set()
            assert self.release_hold.wait(timeout=10), "the held Run was never released"
        if who == self.fail:
            raise RuntimeError("the model fell over")
        return AIMessage(content=f"{who} done")

    async def submit(self, who: str):
        configurable = {"thread_id": f"t-{who}"}
        if who != "chat":
            configurable["skill_run"] = {"name": "reconcile", "fields": {"who": who}, "files": {}}
        async for event in self.agent.astream(
            {"messages": [HumanMessage(content="go")]},
            config={"configurable": configurable},
            stream_mode="custom",
        ):
            self.events[who].append(event["skill_run_status"])

    async def until(self, condition):
        for _ in range(500):
            if condition():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("timed out waiting")

    async def active_is_in_its_model_call(self):
        await asyncio.to_thread(self._in_hold_call.wait, 10)


def test_second_and_third_submissions_queue_behind_the_active_run_and_start_in_order():
    async def scenario():
        s = Scenario(hold="a")
        a = asyncio.create_task(s.submit("a"))
        await s.active_is_in_its_model_call()
        b = asyncio.create_task(s.submit("b"))
        await s.until(lambda: s.events["b"] == ["queued"])
        c = asyncio.create_task(s.submit("c"))
        await s.until(lambda: s.events["c"] == ["queued"])

        assert s.model_calls == ["a"]  # b and c have not started
        assert s.events["a"] == ["running"]  # the active Run was never queued

        s.release_hold.set()
        await asyncio.gather(a, b, c)
        return s

    s = asyncio.run(scenario())

    assert s.model_calls == ["a", "b", "c"]  # submission order
    assert s.events == {"a": ["running"], "b": ["queued", "running"], "c": ["queued", "running"]}


def test_the_next_run_starts_when_the_active_one_fails():
    async def scenario():
        s = Scenario(hold="a", fail="a")
        a = asyncio.create_task(s.submit("a"))
        await s.active_is_in_its_model_call()
        b = asyncio.create_task(s.submit("b"))
        await s.until(lambda: s.events["b"] == ["queued"])

        s.release_hold.set()
        failed, _ = await asyncio.gather(a, b, return_exceptions=True)
        return s, failed

    s, failed = asyncio.run(scenario())

    assert isinstance(failed, RuntimeError)
    assert s.model_calls == ["a", "b"]
    assert s.events["b"] == ["queued", "running"]


def test_the_next_run_starts_when_the_active_one_cannot_even_start():
    async def scenario():
        s = Scenario(hold="nobody")
        gone = s.agent.ainvoke(
            {"messages": [HumanMessage(content="go")]},
            config={"configurable": {"thread_id": "t-gone", "skill_run": {"name": "gone", "fields": {}, "files": {}}}},
        )
        with pytest.raises(library.SkillError):
            await gone
        await asyncio.wait_for(s.submit("b"), 10)  # would hang if the failed Run had kept the slot
        return s

    assert asyncio.run(scenario()).events["b"] == ["running"]


def test_a_run_that_never_reports_back_loses_the_slot_after_its_lease(monkeypatch):
    monkeypatch.setattr(skill_run, "_GATE", skill_run.RunGate(lease_secs=0.3))

    async def scenario():
        s = Scenario(hold="a")
        a = asyncio.create_task(s.submit("a"))  # stuck in its model call: never releases
        await s.active_is_in_its_model_call()
        await asyncio.wait_for(s.submit("b"), 10)  # starts once a's lease runs out
        started_while_a_was_stuck = list(s.model_calls)
        s.release_hold.set()
        await a
        return started_while_a_was_stuck

    assert asyncio.run(scenario()) == ["a", "b"]


def test_a_normal_chat_runs_alongside_the_active_skill_run():
    async def scenario():
        s = Scenario(hold="a")
        a = asyncio.create_task(s.submit("a"))
        await s.active_is_in_its_model_call()
        await s.submit("chat")  # finishes while a is still held
        assert not a.done()
        s.release_hold.set()
        await a
        return s

    s = asyncio.run(scenario())

    assert s.model_calls == ["a", "chat"]
    assert s.events["chat"] == []
