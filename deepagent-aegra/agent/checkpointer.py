"""Durable thread state, in Postgres.

Ported near-verbatim from `agent-runtime/agent/checkpointer.py`
(docs/adr/0005-testing-strategy-carryover-from-agent-runtime.md, point 3) —
same architecture, same concern, unaffected by anything this project dropped.

## Why `make_checkpointer` exists even though `aegra` never calls it

`aegra.json` has no equivalent of `langgraph.json`'s custom
`checkpointer.path` hook: `aegra`'s own `checkpointer` config key only
controls TTL/sweep behaviour, and a served run always persists through
`aegra`'s own Postgres-backed saver (`DATABASE_URL` passed straight to the
`aegra-host` container — see `tests/test_aegra_host_config.py`). So
`make_checkpointer` is never invoked by the served graph; it exists for API
parity with the shape `langgraph_api._checkpointer` once accepted (a
callable async context manager yielding a saver) and so a future standalone
entry point has a matching async-loop counterpart to `sync_checkpointer`.

`python -m agent.runner`, the tests, and `scripts/verify_stack.py` own their
own loop, so they take `sync_checkpointer()` directly and pass the result to
`build_agent`.

Both call `setup()`, which is idempotent (`CREATE TABLE IF NOT EXISTS` plus a
migration-version row), so first-run table creation needs no separate step.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from langgraph.checkpoint.base import BaseCheckpointSaver

logger = logging.getLogger(__name__)

MAX_POOL_SIZE = 20
"""Connections the pool may open.

Graph steps can run concurrently, so a single connection would serialise
every checkpoint write. Well under Postgres' default `max_connections` of
100 to leave room for the sidecar and for psql.
"""


def database_url() -> str | None:
    """`DATABASE_URL`, or `None` when durable state is not configured.

    Read at call time rather than through `Settings`: `.env` may load after
    import, and the checkpointer is built during process startup.
    """
    value = (os.getenv("DATABASE_URL") or "").strip()
    return value or None


@asynccontextmanager
async def make_checkpointer() -> "AsyncIterator[BaseCheckpointSaver]":
    """An async-context-manager checkpointer, for a future async standalone entry point.

    See the module docstring: `aegra` never loads this — it always persists
    through its own Postgres-backed saver.
    """
    url = database_url()
    if not url:
        msg = (
            "DATABASE_URL is not set — thread state will not be durable. "
            "Set it in .env (see .env.example) to checkpoint to Postgres."
        )
        raise RuntimeError(msg)

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    async with AsyncConnectionPool(
        conninfo=url,
        max_size=MAX_POOL_SIZE,
        open=False,
        # Both are required by the Postgres saver: it issues DDL and multi-row
        # reads that assume no enclosing transaction and dict-shaped rows.
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    ) as pool:
        await pool.open(wait=True)
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
        logger.info("Checkpointing thread state to Postgres (%s).", _safe_target(url))
        yield saver


@contextmanager
def sync_checkpointer() -> "Iterator[BaseCheckpointSaver]":
    """A checkpointer for code that owns its own loop.

    Falls back to `InMemorySaver` when `DATABASE_URL` is unset, so an
    unattended run or a test never fails for want of a database.
    """
    url = database_url()
    if not url:
        from langgraph.checkpoint.memory import InMemorySaver

        logger.warning("DATABASE_URL is not set — using in-memory state for this run.")
        yield InMemorySaver()
        return

    from langgraph.checkpoint.postgres import PostgresSaver

    with PostgresSaver.from_conn_string(url) as saver:
        saver.setup()
        logger.info("Checkpointing thread state to Postgres (%s).", _safe_target(url))
        yield saver


def state_target() -> str:
    """Where thread state is going, in one line fit for a log or a banner.

    Credentials are stripped: this string is printed, and a `DATABASE_URL`
    holds a password.
    """
    url = database_url()
    return _safe_target(url) if url else "in-memory"


def _safe_target(url: str) -> str:
    """`host:port/dbname` from a connection URL, with the credentials dropped."""
    tail = url.rsplit("@", 1)[-1]
    return tail.split("?", 1)[0]


__all__ = ["database_url", "make_checkpointer", "state_target", "sync_checkpointer"]
