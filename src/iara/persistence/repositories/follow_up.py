"""Follow-up queue repository — durable scheduling for future messages.

The follow-up scheduler worker polls this table for due items and enqueues
them to the provider_command_outbox. All content fields are opaque hashes —
no raw message text, phone numbers, or PII is stored (INV-05).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from iara.observability.logging import get_logger
from iara.persistence.models import FollowUpQueueItem

logger = get_logger(__name__)


class FollowUpRepository:
    """Manages the follow_up_queue table.

    Args:
        session: An active async SQLAlchemy session.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue_raw(self, payload: dict[str, Any]) -> str:
        """Insert a follow-up item from a raw payload dict.

        Idempotent: duplicate idempotency_key is silently ignored.

        Args:
            payload: Dict with keys matching FollowUpQueueItem columns.

        Returns:
            str: UUID of the created (or existing) item.
        """
        item_id = uuid.uuid4()
        now = datetime.now(UTC)

        trigger_at = payload.get("trigger_at")
        if isinstance(trigger_at, str):
            trigger_at = datetime.fromisoformat(trigger_at)

        stmt = (
            pg_insert(FollowUpQueueItem)
            .values(
                id=item_id,
                tenant_id=uuid.UUID(str(payload["tenant_id"])),
                conversation_id=str(payload.get("conversation_id", "")),
                contact_ref=str(payload.get("contact_ref", "")),
                message_ref=str(payload.get("message_ref", "")),
                message_length=int(payload.get("message_length", 0)),
                reason_ref=str(payload.get("reason_ref", "")),
                trigger_at=trigger_at or (now + timedelta(hours=1)),
                status="pending",
                attempt_count=0,
                max_attempts=int(payload.get("max_attempts", 3)),
                opted_out=bool(payload.get("opted_out", False)),
                correlation_id=str(payload.get("correlation_id", "")),
                idempotency_key=str(payload["idempotency_key"]),
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(constraint="uq_follow_up_idempotency")
        )
        await self._session.execute(stmt)
        return str(item_id)

    async def fetch_due(
        self,
        now: datetime,
        batch_size: int = 50,
    ) -> list[FollowUpQueueItem]:
        """Fetch pending items with trigger_at <= now, ordered by trigger_at.

        Args:
            now: Current UTC datetime for comparison.
            batch_size: Maximum number of items to return.

        Returns:
            list[FollowUpQueueItem]: Due items ready to send.
        """
        stmt = (
            select(FollowUpQueueItem)
            .where(
                FollowUpQueueItem.status == "pending",
                FollowUpQueueItem.trigger_at <= now,
            )
            .order_by(FollowUpQueueItem.trigger_at)
            .limit(batch_size)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def mark_sent(self, item_id: str) -> None:
        """Mark an item as sent.

        Args:
            item_id: UUID string of the item.
        """
        stmt = (
            update(FollowUpQueueItem)
            .where(FollowUpQueueItem.id == uuid.UUID(item_id))
            .values(status="sent", sent_at=datetime.now(UTC), updated_at=datetime.now(UTC))
        )
        await self._session.execute(stmt)

    async def mark_skipped(self, item_id: str, reason: str) -> None:
        """Mark an item as skipped with a reason.

        Args:
            item_id: UUID string of the item.
            reason: Short reason code (opt-out, quiet_hours, max_attempts).
        """
        stmt = (
            update(FollowUpQueueItem)
            .where(FollowUpQueueItem.id == uuid.UUID(item_id))
            .values(
                status="skipped",
                skip_reason=reason[:256],
                updated_at=datetime.now(UTC),
            )
        )
        await self._session.execute(stmt)

    async def mark_failed(self, item_id: str, error: str) -> None:
        """Mark an item as failed.

        Args:
            item_id: UUID string of the item.
            error: Sanitized error description.
        """
        stmt = (
            update(FollowUpQueueItem)
            .where(FollowUpQueueItem.id == uuid.UUID(item_id))
            .values(
                status="failed",
                skip_reason=error[:256],
                updated_at=datetime.now(UTC),
            )
        )
        await self._session.execute(stmt)

    async def increment_attempt(self, item_id: str) -> int:
        """Increment attempt_count and return the new value.

        Args:
            item_id: UUID string of the item.

        Returns:
            int: New attempt count after increment.
        """
        stmt = (
            update(FollowUpQueueItem)
            .where(FollowUpQueueItem.id == uuid.UUID(item_id))
            .values(
                attempt_count=FollowUpQueueItem.attempt_count + 1,
                updated_at=datetime.now(UTC),
            )
            .returning(FollowUpQueueItem.attempt_count)
        )
        result = await self._session.execute(stmt)
        row = result.fetchone()
        return int(row[0]) if row else 0

    async def mark_opted_out(self, conversation_id: str, tenant_id: str) -> int:
        """Set opted_out=True for all pending items in this conversation.

        Args:
            conversation_id: The conversation identifier.
            tenant_id: Tenant UUID string.

        Returns:
            int: Number of items updated.
        """
        stmt = (
            update(FollowUpQueueItem)
            .where(
                FollowUpQueueItem.conversation_id == conversation_id,
                FollowUpQueueItem.tenant_id == uuid.UUID(tenant_id),
                FollowUpQueueItem.status == "pending",
            )
            .values(opted_out=True, updated_at=datetime.now(UTC))
        )
        result = await self._session.execute(stmt)
        count = result.rowcount or 0  # type: ignore[attr-defined]
        logger.info(
            "follow_up_opted_out",
            conversation_id=conversation_id,
            updated_count=count,
        )
        return count
