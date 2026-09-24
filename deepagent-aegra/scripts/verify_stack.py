"""Prove the stack's live dependencies actually work, before an agent depends on them.

    python scripts/verify_stack.py

Checks, in order, printing PASS/FAIL for each:

  1. file store    — the dedicated on-disk directory (docs/adr/0002) round-trips
                      a write/read/delete
  2. checkpointer   — a checkpoint written to Postgres is readable through a
                      second, independent connection
  3. Ollama         — the configured daemon is reachable and OLLAMA_MODEL is
                      one of the models it has pulled

Deliberately kept out of `pytest` (docs/adr/0005): every check here needs a
real, running dependency — Postgres, a local Ollama daemon — and `pytest`
must stay fully offline. Reads its configuration from `.env` exactly as the
runtime does, so a PASS here means the runtime is pointed at working
infrastructure, not that some other credentials happen to work.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    return ok


def check_file_store(settings) -> None:  # noqa: ANN001
    print("\n[1] file store round-trip")
    settings.file_store_dir.mkdir(parents=True, exist_ok=True)

    marker = f"verify-{uuid.uuid4().hex[:8]}.txt"
    body = f"written by verify_stack {uuid.uuid4().hex}"
    path = settings.file_store_dir / marker

    try:
        path.write_text(body)
        record("write", True, str(path))
    except OSError as exc:
        record("write", False, str(exc))
        return

    got = path.read_text()
    record("read back matches", got == body)

    path.unlink()
    record("delete removes the file", not path.exists())


def check_checkpointer() -> None:
    print("\n[2] checkpointer")
    from agent.checkpointer import database_url, state_target

    if not database_url():
        record("DATABASE_URL set", False, "unset — thread state would not be durable")
        return
    record("DATABASE_URL set", True, state_target())

    from langgraph.checkpoint.base import empty_checkpoint

    from agent.checkpointer import sync_checkpointer

    thread_id = f"verify-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    checkpoint = empty_checkpoint()
    token = uuid.uuid4().hex
    checkpoint["channel_values"] = {"verify_token": token}

    try:
        with sync_checkpointer() as saver:
            saver.put(config, checkpoint, {"source": "update", "step": 1}, {})
        record("setup() + put()", True, f"thread {thread_id}")
    except Exception as exc:  # noqa: BLE001
        record("setup() + put()", False, str(exc))
        return

    # A second saver means a second connection: this reads Postgres, not a cache.
    try:
        with sync_checkpointer() as reader:
            tuple_ = reader.get_tuple({"configurable": {"thread_id": thread_id}})
    except Exception as exc:  # noqa: BLE001
        record("read back on a fresh connection", False, str(exc))
        return

    got = (tuple_.checkpoint.get("channel_values") or {}).get("verify_token") if tuple_ else None
    record("read back on a fresh connection", got == token, f"token {got}")


def check_ollama(settings) -> None:  # noqa: ANN001
    print("\n[3] Ollama daemon")
    try:
        from ollama import Client
    except ImportError as exc:  # pragma: no cover - optional dependency
        record("reachable", False, f"ollama client not installed: {exc}")
        return

    try:
        response = Client(host=settings.ollama_base_url).list()
    except Exception as exc:  # noqa: BLE001 - the point is to report, not to raise
        record("reachable", False, f"{settings.ollama_base_url}: {exc}")
        return
    record("reachable", True, settings.ollama_base_url)

    names = {model.model for model in response.models}
    record(
        "OLLAMA_MODEL is pulled",
        settings.ollama_model in names,
        f"{settings.ollama_model!r} in {sorted(names)}",
    )


def main() -> int:
    from dotenv import load_dotenv  # noqa: PLC0415

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    from agent.config import get_settings

    settings = get_settings()
    print(
        f"OLLAMA_MODEL={settings.ollama_model}  "
        f"OLLAMA_BASE_URL={settings.ollama_base_url}  "
        f"FILE_STORE_DIR={settings.file_store_dir}"
    )

    check_file_store(settings)
    check_checkpointer()
    check_ollama(settings)

    failed = [name for name, ok, _ in _results if not ok]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} passed")
    if failed:
        print("failed: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
