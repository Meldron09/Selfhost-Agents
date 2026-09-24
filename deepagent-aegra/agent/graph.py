"""The runtime: a trivial deepagents graph, with no subagents yet.

This is the ground the rest of the spec (#13) builds on — an orchestrator
that reads and writes nothing yet. `file-reader`, `output-writer`, and
`web-search` are later tickets (#15-#18); registering them here would be
domain behavior this ticket deliberately doesn't own.
"""
from __future__ import annotations

from deepagents import create_deep_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from agent.model import get_model

SYSTEM_PROMPT = """You are the orchestrator for deepagent-aegra, a file-processing assistant.

Nothing is wired up yet: no file-reading, no file-writing, no web search. If asked to do any of those, say plainly that this deployment does not yet support it."""


def build_agent(
    model: BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Compile the runtime graph.

    Args:
        model: Overrides the configured model plane. Used by tests, which
            pass a `ScriptedChatModel` here rather than setting an env var —
            there is no `MODEL_PROVIDER=fake` dispatch in this project (see
            agent/model.py).
        checkpointer: Only for standalone use (`agent.runner`,
            `scripts/verify_stack.py`). Leave `None` under `aegra serve`,
            which supplies its own persistence.
    """
    model = model or get_model()

    return create_deep_agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
        name="deepagent-aegra",
    )


def make_agent() -> CompiledStateGraph:
    """Entrypoint the server loads: aegra-host's `AEGRA_GRAPH_TARGET` names this.

    Built lazily so the server's `.env` is loaded before `OLLAMA_MODEL`/
    `OLLAMA_CONTEXT_WINDOW` are read. Must stay syscall-free: the server
    calls this on the event loop, and a blocking call here fails the run.
    See tests/test_server_compat.py.
    """
    return build_agent()
