"""Postgres-backed governed semantic memory store.

Replaces the in-memory MemoryStore with a durable implementation that
survives worker restarts and works across multiple workers.

Per the architecture:
- Namespace = tenant_id + optional scope suffix (e.g. "conv_001")
- All items carry TTL, consent_ref, and anonymization flag
- LGPD compliance: purge() and anonymize() are single-row operations
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iara.observability.logging import get_logger
from iara.persistence.models import AgentMemoryItem

logger = get_logger(__name__)


class PostgresMemoryStore:
    """Durable, tenant-scoped semantic memory store backed by Postgres.

    When ``enabled=False`` (the default) all operations are fast no-ops so
    the store is safe to instantiate unconditionally.

    Args:
        session_factory: Async SQLAlchemy session factory.
        tenant_id: The tenant UUID string.
        namespace: Memory namespace (default: tenant_id).
        enabled: Whether memory is active.
        ttl_days: Default TTL for items.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        tenant_id: str,
        namespace: str | None = None,
        enabled: bool = False,
        ttl_days: int = 90,
    ) -> None:
        self._session_factory = session_factory
        self._tenant_id = tenant_id
        self._namespace = namespace or tenant_id
        self._enabled = enabled
        self._ttl_days = ttl_days

    # ── Write ─────────────────────────────────────────────────────────────────

    async def store(
        self,
        key: str,
        content: str,
        consent_ref: str | None = None,
    ) -> bool:
        """Upsert a memory item.

        Args:
            key: Unique key within the namespace.
            content: Sanitized memory content (no PII).
            consent_ref: Reference to the consent record.

        Returns:
            bool: True if stored, False when memory is disabled.
        """
        if not self._enabled:
            logger.debug("memory_disabled_skip_store", key=key)
            return False

        now = datetime.now(UTC)
        expires_at = now + timedelta(days=self._ttl_days)

        async with self._session_factory() as session:
            # Try update first, insert if not found
            result = await session.execute(
                select(AgentMemoryItem).where(
                    AgentMemoryItem.namespace == self._namespace,
                    AgentMemoryItem.item_key == key,
                )
            )
            existing = result.scalar_one_or_none()
            if existing is not None:
                existing.content = content
                existing.updated_at = now
                existing.expires_at = expires_at
                existing.consent_ref = consent_ref
            else:
                session.add(
                    AgentMemoryItem(
                        id=uuid.uuid4(),
                        tenant_id=uuid.UUID(self._tenant_id),
                        namespace=self._namespace,
                        item_key=key,
                        content=content,
                        ttl_days=self._ttl_days,
                        expires_at=expires_at,
                        consent_ref=consent_ref,
                    )
                )
            await session.commit()

        logger.info("memory_stored", key=key, namespace=self._namespace)
        return True

    # ── Read ──────────────────────────────────────────────────────────────────

    async def retrieve(self, key: str) -> AgentMemoryItem | None:
        """Retrieve a memory item by key.

        Returns None if memory is disabled, item is expired, or not found.

        Args:
            key: The item key.

        Returns:
            AgentMemoryItem | None: The item ORM record, or None.
        """
        if not self._enabled:
            return None

        async with self._session_factory() as session:
            result = await session.execute(
                select(AgentMemoryItem).where(
                    AgentMemoryItem.namespace == self._namespace,
                    AgentMemoryItem.item_key == key,
                    AgentMemoryItem.expires_at > datetime.now(UTC),
                )
            )
            return result.scalar_one_or_none()

    async def list_recent(self, limit: int = 10) -> list[AgentMemoryItem]:
        """List the most recently updated memory items in the namespace.

        Args:
            limit: Maximum number of items to return.

        Returns:
            list[AgentMemoryItem]: Items ordered by updated_at descending.
        """
        if not self._enabled:
            return []

        async with self._session_factory() as session:
            result = await session.execute(
                select(AgentMemoryItem)
                .where(
                    AgentMemoryItem.namespace == self._namespace,
                    AgentMemoryItem.expires_at > datetime.now(UTC),
                    AgentMemoryItem.is_anonymized.is_(False),
                )
                .order_by(AgentMemoryItem.updated_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    # ── LGPD compliance ───────────────────────────────────────────────────────

    async def purge(self, key: str) -> bool:
        """Permanently delete a memory item (LGPD erasure).

        Args:
            key: The item key.

        Returns:
            bool: True if a row was deleted.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                delete(AgentMemoryItem).where(
                    AgentMemoryItem.namespace == self._namespace,
                    AgentMemoryItem.item_key == key,
                )
            )
            await session.commit()
            deleted = (result.rowcount or 0) > 0
            if deleted:
                logger.info("memory_purged", key=key, namespace=self._namespace)
            return deleted

    async def purge_tenant(self) -> int:
        """Permanently delete ALL memory items for this tenant (LGPD erasure).

        Returns:
            int: Number of rows deleted.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                delete(AgentMemoryItem).where(
                    AgentMemoryItem.tenant_id == uuid.UUID(self._tenant_id),
                )
            )
            await session.commit()
            deleted = result.rowcount or 0
            logger.info("memory_tenant_purged", tenant_ref=self._tenant_id[:8], count=deleted)
            return deleted

    async def anonymize(self, key: str) -> bool:
        """Replace memory content with an anonymized placeholder.

        Args:
            key: The item key.

        Returns:
            bool: True if an item was anonymized.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                update(AgentMemoryItem)
                .where(
                    AgentMemoryItem.namespace == self._namespace,
                    AgentMemoryItem.item_key == key,
                )
                .values(content="[ANONYMIZED]", is_anonymized=True)
            )
            await session.commit()
            updated = (result.rowcount or 0) > 0
            if updated:
                logger.info("memory_anonymized", key=key, namespace=self._namespace)
            return updated
