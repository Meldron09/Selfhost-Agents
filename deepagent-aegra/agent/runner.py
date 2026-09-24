"""Standalone runner: execute one request end to end and stream the steps.

`aegra serve` is the path once a UI is attached; this is the path before
that, and the one to use for a long unattended run. Mirrors
`agent-runtime/agent/runner.py`'s shape, trimmed of anything (workspace,
skills, sandbox) this project's trivial graph doesn't have yet.

    python -m agent.runner "hello"
    python -m agent.runner --thread demo --file request.txt
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from agent.checkpointer import state_target, sync_checkpointer
from agent.graph import build_agent


def _describe(message: BaseMessage) -> str | None:
    """One line per step, so a long run reads as a narrative."""
    if isinstance(message, AIMessage):
        lines = []
        for call in message.tool_calls or []:
            args = call.get("args", {})
            detail = args.get("query") or args.get("description") or ""
            if isinstance(detail, str) and len(detail) > 140:
                detail = detail[:140] + "…"
            lines.append(f"  → {call['name']}({detail})" if detail else f"  → {call['name']}")
        text = message.text() if hasattr(message, "text") else str(message.content)
        if text and text.strip():
            lines.append(f"  {text.strip()}")
        return "\n".join(lines) or None
    if isinstance(message, ToolMessage):
        body = str(message.content).strip().replace("\n", " ")
        status = "!" if message.status == "error" else "✓"
        return f"  {status} {message.name}: {body[:200]}{'…' if len(body) > 200 else ''}"
    return None


def run(request: str, thread_id: str | None = None) -> int:
    thread_id = thread_id or f"run-{uuid.uuid4().hex[:8]}"

    # Standalone runs need their own persistence; under `aegra serve` the
    # server owns it and `build_agent` is called without a checkpointer.
    with sync_checkpointer() as checkpointer:
        agent = build_agent(checkpointer=checkpointer)

        print(f"state={state_target()}", file=sys.stderr)
        print(f"thread={thread_id}\n", file=sys.stderr)

        final: BaseMessage | None = None
        for chunk in agent.stream(
            {"messages": [HumanMessage(content=request)]},
            config={"configurable": {"thread_id": thread_id}},
            stream_mode="values",
        ):
            messages = chunk.get("messages") or []
            if not messages or messages[-1] is final:
                continue
            final = messages[-1]
            line = _describe(final)
            if line:
                print(line, flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one request through deepagent-aegra.")
    parser.add_argument("request", nargs="*", help="The request text.")
    parser.add_argument("--file", type=Path, help="Read the request from a file instead.")
    parser.add_argument("--thread", help="Thread id; reuse it to continue a run.")
    args = parser.parse_args()

    if args.file:
        request = args.file.read_text()
    elif args.request:
        request = " ".join(args.request)
    else:
        parser.error("Provide a request, or --file.")
    return run(request, args.thread)


if __name__ == "__main__":
    raise SystemExit(main())
