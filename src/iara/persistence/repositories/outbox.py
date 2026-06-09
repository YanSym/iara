"""Provider command outbox repository.

The outbox is the durability layer for external side effects. Commands are
inserted here during graph execution and drained by the outbox worker.
This ensures effectively-once execution even under retries and checkpointing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from iara.contracts.provider import ProviderCommand
from iara.observability.logging import get_logger
from iara.persistence.models import ProviderCommandOutbox

logger = get_logger(__name__)


class OutboxRepository:
    """Manages the provider command outbox.

    Args:
        session: An active async SQLAlchemy session.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(self, command: ProviderCommand) -> bool:
        """Add a provider command to the outbox.

        Uses an upsert to handle idempotent re-queuing.

        Args:
            command: The provider command to queue.

        Returns:
            bool: True if this was a new insertion, False if already exists.
        """
        stmt = (
            pg_insert(ProviderCommandOutbox)
            .values(
                id=uuid.uuid4(),
                tenant_id=command.tenant_id,
                command_id=command.command_id,
                idempotency_key=command.idempotency_key,
                correlation_id=command.correlation_id,
                provider=command.provider,
                capability_name=command.capability_name,
                parameters_json=command.parameters,
                risk_class=command.risk_class.value,
                status="pending",
                retry_count=0,
                scheduled_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(constraint="uq_outbox_idempotency")
        )
        result = await self._session.execute(stmt)
        return bool(result.rowcount > 0)  # type: ignore[attr-defined]

    async def fetch_pending(
        self, tenant_id: uuid.UUID, limit: int = 10
    ) -> list[ProviderCommandOutbox]:
        """Fetch pending commands for a specific tenant ordered by scheduled_at.

        Args:
            tenant_id: The tenant UUID.
            limit: Maximum number of commands to fetch.

        Returns:
            list[ProviderCommandOutbox]: Pending outbox records.
        """
        stmt = (
            select(ProviderCommandOutbox)
            .where(
                ProviderCommandOutbox.tenant_id == tenant_id,
                ProviderCommandOutbox.status == "pending",
            )
            .order_by(ProviderCommandOutbox.scheduled_at)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def fetch_pending_all(self, limit: int = 50) -> list[ProviderCommandOutbox]:
        """Fetch pending commands across all tenants ordered by scheduled_at.

        Used by the outbox drainer worker which processes commands globally.
        Only returns commands whose scheduled_at is in the past, so that
        retry backoff delays are respected.

        Args:
            limit: Maximum number of commands to fetch.

        Returns:
            list[ProviderCommandOutbox]: Pending outbox records across all tenants.
        """
        now = datetime.now(UTC)
        stmt = (
            select(ProviderCommandOutbox)
            .where(
                ProviderCommandOutbox.status == "pending",
                ProviderCommandOutbox.scheduled_at <= now,
            )
            .order_by(ProviderCommandOutbox.scheduled_at)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def mark_sent(self, command_id: str) -> None:
        """Mark a command as sent.

        Args:
            command_id: The command ID.
        """
        stmt = (
            update(ProviderCommandOutbox)
            .where(ProviderCommandOutbox.command_id == command_id)
            .values(status="sent", sent_at=datetime.now(UTC))
        )
        await self._session.execute(stmt)

    async def mark_confirmed(self, command_id: str) -> None:
        """Mark a command as confirmed (readback verified).

        Args:
            command_id: The command ID.
        """
        stmt = (
            update(ProviderCommandOutbox)
            .where(ProviderCommandOutbox.command_id == command_id)
            .values(status="confirmed", confirmed_at=datetime.now(UTC))
        )
        await self._session.execute(stmt)

    async def mark_failed(
        self, command_id: str, increment_retry: bool = True, reason: str | None = None
    ) -> None:
        """Mark a command as failed and optionally increment retry count.

        Args:
            command_id: The command ID.
            increment_retry: Whether to increment the retry counter.
            reason: Optional failure reason (truncated to 500 chars in storage).
        """
        if reason:
            # No failure_reason column in schema yet — surface via log so it's not lost.
            logger.warning("outbox_command_failed", command_id=command_id, reason=reason[:500])
        values: dict[str, Any] = {"status": "failed"}
        if increment_retry:
            # Use raw SQL increment
            stmt = (
                update(ProviderCommandOutbox)
                .where(ProviderCommandOutbox.command_id == command_id)
                .values(
                    status="failed",
                    retry_count=ProviderCommandOutbox.retry_count + 1,
                )
            )
        else:
            stmt = (
                update(ProviderCommandOutbox)
                .where(ProviderCommandOutbox.command_id == command_id)
                .values(**values)
            )
        await self._session.execute(stmt)

    async def mark_failed_for_retry(
        self,
        command_id: str,
        retry_delay_seconds: int = 30,
    ) -> None:
        """Increment retry count and reschedule a failed command for a future attempt.

        Resets status to 'pending' with an exponential-ish delay so the drainer
        picks it up again after the backoff window, rather than abandoning it.

        Args:
            command_id: The command ID.
            retry_delay_seconds: Seconds to wait before the next attempt.
        """
        next_at = datetime.now(UTC) + timedelta(seconds=retry_delay_seconds)
        stmt = (
            update(ProviderCommandOutbox)
            .where(ProviderCommandOutbox.command_id == command_id)
            .values(
                status="pending",
                retry_count=ProviderCommandOutbox.retry_count + 1,
                scheduled_at=next_at,
            )
        )
        await self._session.execute(stmt)

    async def mark_dead_lettered(self, command_id: str, reason: str | None = None) -> None:
        """Mark a command as dead-lettered (max retries exceeded).

        Args:
            command_id: The command ID.
            reason: Optional reason for dead-lettering.
        """
        stmt = (
            update(ProviderCommandOutbox)
            .where(ProviderCommandOutbox.command_id == command_id)
            .values(status="dead_lettered")
        )
        await self._session.execute(stmt)
