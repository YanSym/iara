"""Idempotency ledger repository.

Records processed event idempotency keys to prevent duplicate processing.
The ledger is the authoritative source for whether an event has been seen.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from iara.persistence.models import EventReceipt


class IdempotencyRepository:
    """Manages the idempotency ledger for received events.

    Args:
        session: An active async SQLAlchemy session.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_duplicate(self, tenant_id: uuid.UUID, idempotency_key: str) -> bool:
        """Check if an idempotency key has already been processed.

        Args:
            tenant_id: The tenant UUID.
            idempotency_key: The key to check.

        Returns:
            bool: True if the key has been seen before.
        """
        stmt = select(EventReceipt.id).where(
            EventReceipt.tenant_id == tenant_id,
            EventReceipt.idempotency_key == idempotency_key,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def record(
        self,
        tenant_id: uuid.UUID,
        idempotency_key: str,
        raw_hash: str,
        correlation_id: str,
    ) -> bool:
        """Record an idempotency key as processed.

        Uses an upsert to handle concurrent inserts safely.

        Args:
            tenant_id: The tenant UUID.
            idempotency_key: The key to record.
            raw_hash: SHA-256 hash of the raw payload.
            correlation_id: Correlation ID for tracing.

        Returns:
            bool: True if this was a new record, False if it was a duplicate.
        """
        stmt = (
            pg_insert(EventReceipt)
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                raw_hash=raw_hash,
                correlation_id=correlation_id,
                status="received",
                received_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(
                constraint="uq_event_receipt",
            )
        )
        result = await self._session.execute(stmt)
        return bool(result.rowcount > 0)  # type: ignore[attr-defined]
