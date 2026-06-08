"""Database connection and session management.

Uses SQLAlchemy 2.0 async engine with asyncpg. The Database class manages
the engine lifecycle and provides session factories for repositories.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from iara.observability.logging import get_logger

logger = get_logger(__name__)


class Database:
    """Manages the async SQLAlchemy engine and session factory.

    Args:
        url: Async database URL (postgresql+asyncpg://...).
        pool_size: Connection pool size.
        max_overflow: Maximum pool overflow.
        echo: Whether to echo SQL statements (never in production).
    """

    def __init__(
        self,
        url: str,
        pool_size: int = 10,
        max_overflow: int = 20,
        echo: bool = False,
    ) -> None:
        self._url = url
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self._echo = echo
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    async def connect(self) -> None:
        """Initialize the database engine and session factory.

        Must be called once before using any sessions.
        """
        self._engine = create_async_engine(
            self._url,
            pool_size=self._pool_size,
            max_overflow=self._max_overflow,
            echo=self._echo,
            pool_pre_ping=True,
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        logger.info("database_connected", pool_size=self._pool_size)

    async def disconnect(self) -> None:
        """Dispose the engine and close all connections."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            logger.info("database_disconnected")

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession]:
        """Provide a transactional database session.

        Yields:
            AsyncSession: An active database session.

        Raises:
            RuntimeError: If the database is not connected.
        """
        if self._session_factory is None:
            raise RuntimeError("Database is not connected. Call connect() first.")
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @property
    def engine(self) -> AsyncEngine:
        """Return the async engine.

        Raises:
            RuntimeError: If the database is not connected.
        """
        if self._engine is None:
            raise RuntimeError("Database is not connected. Call connect() first.")
        return self._engine


# ── Application-level singleton ──────────────────────────────────────────────

_database: Database | None = None


def get_database() -> Database:
    """Return the application database singleton.

    Returns:
        Database: The application database instance.

    Raises:
        RuntimeError: If the database has not been initialized.
    """
    if _database is None:
        raise RuntimeError("Database singleton not initialized. Call init_database() first.")
    return _database


def init_database(
    url: str, pool_size: int = 10, max_overflow: int = 20, echo: bool = False
) -> Database:
    """Initialize the application database singleton.

    Args:
        url: Async database URL.
        pool_size: Connection pool size.
        max_overflow: Maximum pool overflow.
        echo: Whether to echo SQL (for debugging only).

    Returns:
        Database: The initialized database instance.
    """
    global _database
    _database = Database(url=url, pool_size=pool_size, max_overflow=max_overflow, echo=echo)
    return _database
