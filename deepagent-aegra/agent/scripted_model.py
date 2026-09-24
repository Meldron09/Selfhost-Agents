"""A scripted chat model, so the runtime can be exercised without a model plane.

Ported near-verbatim from `agent-runtime/agent/scripted_model.py`
(docs/adr/0005-testing-strategy-carryover-from-agent-runtime.md, point 1) — kept
orthogonal to the settled Ollama-only *production* model plane, which governs
what serves runs, not what drives tests.

None of `agent-runtime`'s `demo_responder`/`DEMO_SCRIPT` carries over: those
script a skill-runtime-specific demo (sandbox execution, skill loading,
`outputs/`) this project doesn't have. Each test here supplies its own
`responder`.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool

Responder = Callable[[list[BaseMessage], list[str]], AIMessage]


class ScriptedChatModel(BaseChatModel):
    """Returns pre-scripted assistant turns, including tool calls.

    Provide either `script` (a fixed sequence of turns, replayed in order) or
    `responder` (a callable that sees the conversation and the bound tool names
    and decides the next turn). `responder` wins when both are set.
    """

    script: list[AIMessage] = []
    responder: Responder | None = None
    bound_tools: list[str] = []
    final_text: str = "Done."
    calls: int = 0

    model_config = {"arbitrary_types_allowed": True}

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable | BaseTool],
        **kwargs: Any,
    ) -> Runnable[Any, BaseMessage]:
        """Record the tool names and return self, so the agent loop can run."""
        names = [getattr(t, "name", None) or getattr(t, "__name__", str(t)) for t in tools]
        return self.model_copy(update={"bound_tools": names})

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.responder is not None:
            message = self.responder(messages, self.bound_tools)
        elif self.calls < len(self.script):
            message = self.script[self.calls]
        else:
            message = AIMessage(content=self.final_text)
        object.__setattr__(self, "calls", self.calls + 1)
        return ChatResult(generations=[ChatGeneration(message=message)])
