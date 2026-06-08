"""Debounce repository — prevents rapid-fire conversation processing."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from iara.persistence.models import ConversationDebounce


class DebounceRepository:
    """Manages per-conversation debounce windows.

    When a conversation is in the debounce window, new events for that
    conversation are queued but not immediately processed. The window
    prevents multiple rapid messages from triggering multiple agent runs.

    Args:
        session: An active async SQLAlchemy session.
        debounce_seconds: How long to hold the debounce window.
    """

    def __init__(self, session: AsyncSession, debounce_seconds: int = 3) -> None:
        self._session = session
        self._debounce_seconds = debounce_seconds

    async def is_debouncing(self, tenant_id: uuid.UUID, conversation_id: str) -> bool:
        """Check if a conversation is currently in the debounce window.

        Args:
            tenant_id: The tenant UUID.
            conversation_id: The conversation identifier.

        Returns:
            bool: True if the conversation is debouncing.
        """
        now = datetime.now(UTC)
        stmt = select(ConversationDebounce.id).where(
            ConversationDebounce.tenant_id == tenant_id,
            ConversationDebounce.conversation_id == conversation_id,
            ConversationDebounce.locked_until > now,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def set_debounce(self, tenant_id: uuid.UUID, conversation_id: str) -> None:
        """Set or refresh the debounce window for a conversation.

        Args:
            tenant_id: The tenant UUID.
            conversation_id: The conversation identifier.
        """
        locked_until = datetime.now(UTC) + timedelta(seconds=self._debounce_seconds)
        stmt = (
            pg_insert(ConversationDebounce)
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                locked_until=locked_until,
                created_at=datetime.now(UTC),
            )
            .on_conflict_do_update(
                constraint="uq_debounce_conversation",
                set_={"locked_until": locked_until},
            )
        )
        await self._session.execute(stmt)

    async def clear_debounce(self, tenant_id: uuid.UUID, conversation_id: str) -> None:
        """Clear the debounce window for a conversation.

        Args:
            tenant_id: The tenant UUID.
            conversation_id: The conversation identifier.
        """
        stmt = delete(ConversationDebounce).where(
            ConversationDebounce.tenant_id == tenant_id,
            ConversationDebounce.conversation_id == conversation_id,
        )
        await self._session.execute(stmt)
