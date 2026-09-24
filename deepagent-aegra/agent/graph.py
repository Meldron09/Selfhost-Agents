"""The runtime: orchestrator + `output-writer` (#16) + `file-reader` (#17) +
`web-search` (#18, gated per-run by `WebSearchGateMiddleware`).
"""
from __future__ import annotations

from deepagents import create_deep_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, SummarizationMiddleware
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from agent.attachment_ack import AttachmentAcknowledgeMiddleware
from agent.config import Settings, get_settings
from agent.model import get_model
from agent.state import DeepAgentAegraState
from agent.subagents import build_subagents
from agent.web_search_gate import WebSearchGateMiddleware

SYSTEM_PROMPT = """You are the orchestrator for deepagent-aegra, a file-processing assistant. You read files people attach and produce files people ask for by delegating to specialists — you never touch file bytes yourself.

## Reading attachments

Every Attachment goes through `file-reader` via `task`, unconditionally: you cannot read a file's content yourself. Acknowledge every Attachment by name in your first reply, whatever else that reply says. Include the Attachment's filename and key in the task description you give `file-reader`, along with what you actually need to know from it.

Attachments stay listed for the rest of the thread, not just the turn they arrived on. Delegate each one to `file-reader` the first time you see it. Don't re-delegate an Attachment you already had `file-reader` read earlier in this same thread unless the person is now asking something new about it — re-reading a file that hasn't changed wastes a turn without changing the answer.

## Producing files

Any file you produce goes through `output-writer` via `task`, unconditionally: you cannot write a file yourself. Pass it the structured content in the shape it expects (a table stays a table, slides stay slides — don't flatten or improvise the shape to save a step). If the content came from `file-reader`, hand it through in the same shape it came back in — do not reshape a table into prose or split prose into fake slides.

## Relaying results

Relay what a subagent actually reports, not a smoothed-over version. If `file-reader` reports a file it couldn't read — an unsupported type, an unknown key, or content that wouldn't parse — say so plainly, by filename, exactly as reported. If `output-writer` reports a file it couldn't handle, say so the same way. Never describe a file as read or written when the subagent reported it wasn't, and never answer a question about an attachment's content unless `file-reader` actually extracted it.

The person receives a finished Output automatically once it's registered — you don't construct or state a path, link, or location for it. Confirm in prose what was produced; do not invent where to find it."""

_SUMMARIZE_AT_FRACTION = 0.8
_SUMMARIZE_AT_TOKENS = 150_000  # used when the model declares no context window
_KEEP_LAST_MESSAGES = 20


def _summarization_trigger(model: BaseChatModel) -> tuple[str, float] | tuple[str, int]:
    """When to compact the history.

    Mirrors `agent-runtime`'s exact trigger logic
    (docs/adr/0003-synthetic-profile-shim-for-ollama-summarization-threshold.md):
    a fraction of the context window when the model declares one via
    `.profile["max_input_tokens"]` (the synthetic shim `agent/model.py`
    attaches to `ChatOllama`), else an absolute token count.
    """
    profile = getattr(model, "profile", None) or {}
    if profile.get("max_input_tokens"):
        return ("fraction", _SUMMARIZE_AT_FRACTION)
    return ("tokens", _SUMMARIZE_AT_TOKENS)


def _build_middleware(settings: Settings, model: BaseChatModel) -> list[AgentMiddleware]:
    """The orchestrator's own middleware stack: `AttachmentAcknowledgeMiddleware`
    (docs/adr/0006) and `WebSearchGateMiddleware` (docs/adr/0007), both new to
    this project, plus `HumanInTheLoopMiddleware`/`SummarizationMiddleware`,
    which are carried over from `agent-runtime` and trimmed to what this
    project actually needs (docs/adr/0005, point 5).
    """
    middleware: list[AgentMiddleware] = [
        AttachmentAcknowledgeMiddleware(),
        WebSearchGateMiddleware(),
    ]

    if settings.require_approval:
        # Carried over with an empty gate set: no tool in this design is
        # approval-gated, so REQUIRE_APPROVAL is inert but present — a
        # scripted run with it set to true never pauses, because nothing is
        # configured to gate.
        middleware.append(HumanInTheLoopMiddleware(interrupt_on={}))

    # A long run can exceed the context window. Compact the history near the
    # limit and keep the recent turns verbatim, rather than failing the run
    # at the point it becomes valuable.
    middleware.append(
        SummarizationMiddleware(
            model=model,
            trigger=_summarization_trigger(model),
            keep=("messages", _KEEP_LAST_MESSAGES),
        )
    )

    return middleware


def build_agent(
    model: BaseChatModel | None = None,
    settings: Settings | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> CompiledStateGraph:
    """Compile the runtime graph.

    Args:
        model: Overrides the configured model plane. Used by tests, which
            pass a `ScriptedChatModel` here rather than setting an env var —
            there is no `MODEL_PROVIDER=fake` dispatch in this project (see
            agent/model.py).
        settings: Overrides environment-derived settings. Used by tests.
        checkpointer: Only for standalone use (`agent.runner`,
            `scripts/verify_stack.py`). Leave `None` under `aegra serve`,
            which supplies its own persistence.
    """
    settings = settings or get_settings()
    model = model or get_model(settings)

    return create_deep_agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        state_schema=DeepAgentAegraState,
        subagents=build_subagents(),
        middleware=_build_middleware(settings, model),
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
