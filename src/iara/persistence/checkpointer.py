"""Postgres-backed LangGraph checkpointer factory.

Creates an AsyncPostgresSaver for production-grade conversation persistence.
In development/test, falls back to MemorySaver with a warning.

Usage (in a long-lived async context such as a worker):

    async with postgres_checkpointer(settings.database_url) as checkpointer:
        graph = build_production_graph(settings, checkpointer=checkpointer)
        await run_worker(graph)
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from iara.observability.logging import get_logger

logger = get_logger(__name__)

_ASYNCPG_RE = re.compile(r"\+asyncpg")

# Import at module level so the class can be patched in tests.
try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver as _AsyncPostgresSaver
except ImportError:
    _AsyncPostgresSaver = None  # type: ignore[assignment,misc]


def _to_psycopg_url(database_url: str) -> str:
    """Strip +asyncpg driver suffix so psycopg3 accepts the URL."""
    return _ASYNCPG_RE.sub("", database_url)


@asynccontextmanager
async def postgres_checkpointer(database_url: str) -> AsyncIterator[Any]:
    """Async context manager that yields a ready-to-use AsyncPostgresSaver.

    Creates the LangGraph checkpoint tables (graph_checkpoints,
    graph_checkpoint_blobs, graph_checkpoint_writes) on first run via
    ``checkpointer.setup()``.

    The checkpointer connection pool remains open for the lifetime of the
    context — perfect for long-lived workers.

    Args:
        database_url: SQLAlchemy asyncpg URL (``postgresql+asyncpg://...``).
            The ``+asyncpg`` suffix is stripped automatically.

    Yields:
        AsyncPostgresSaver: Ready-to-use, schema-initialized checkpointer.

    Raises:
        ImportError: If ``langgraph-checkpoint-postgres`` is not installed.

    Example::

        async with postgres_checkpointer(settings.database_url) as cp:
            graph = build_production_graph(settings, checkpointer=cp)
    """
    if _AsyncPostgresSaver is None:
        raise ImportError(
            "langgraph-checkpoint-postgres is required for Postgres checkpointing. "
            "Add 'langgraph-checkpoint-postgres>=2.0' to your dependencies."
        )

    psycopg_url = _to_psycopg_url(database_url)
    async with _AsyncPostgresSaver.from_conn_string(psycopg_url) as checkpointer:
        await checkpointer.setup()
        logger.info("postgres_checkpointer_initialized")
        yield checkpointer
        logger.info("postgres_checkpointer_closing")
