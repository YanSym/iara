"""Conversation lease repository — exclusive execution locks.

Leases (fencing tokens) prevent concurrent worker processing of the same
conversation. Only one worker can hold a lease for a conversation at a time.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from iara.contracts.errors import LeaseConflictError
from iara.persistence.models import ConversationRunLease


class LeaseRepository:
    """Manages exclusive conversation leases with fencing tokens.

    A lease grants a worker the exclusive right to process a conversation.
    Fencing tokens prevent stale workers from executing side effects after
    their lease has expired.

    Args:
        session: An active async SQLAlchemy session.
        lease_ttl_seconds: How long a lease is valid.
        worker_id: Identifier for the current worker.
    """

    def __init__(
        self,
        session: AsyncSession,
        lease_ttl_seconds: int = 300,
        worker_id: str | None = None,
    ) -> None:
        self._session = session
        self._lease_ttl_seconds = lease_ttl_seconds
        self._worker_id = worker_id or str(uuid.uuid4())

    async def acquire(self, tenant_id: uuid.UUID, conversation_id: str) -> str:
        """Acquire an exclusive lease for a conversation.

        Args:
            tenant_id: The tenant UUID.
            conversation_id: The conversation to lock.

        Returns:
            str: The fencing token for this lease.

        Raises:
            LeaseConflictError: If another worker already holds the lease.
        """
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=self._lease_ttl_seconds)
        fencing_token = str(uuid.uuid4())

        # Check for existing active lease
        stmt = select(ConversationRunLease).where(
            ConversationRunLease.tenant_id == tenant_id,
            ConversationRunLease.conversation_id == conversation_id,
            ConversationRunLease.expires_at > now,
            ConversationRunLease.released_at.is_(None),
        )
        result = await self._session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing is not None and existing.worker_id != self._worker_id:
            raise LeaseConflictError(
                f"Conversation {conversation_id!r} is already leased by another worker",
                conversation_id=conversation_id,
            )

        if existing is not None:
            # Refresh our own lease
            stmt_update = (
                update(ConversationRunLease)
                .where(ConversationRunLease.id == existing.id)
                .values(expires_at=expires_at, fencing_token=fencing_token)
            )
            await self._session.execute(stmt_update)
            return fencing_token

        # Insert new lease (upsert to handle race conditions)
        stmt_insert = (
            pg_insert(ConversationRunLease)
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                fencing_token=fencing_token,
                worker_id=self._worker_id,
                acquired_at=now,
                expires_at=expires_at,
            )
            .on_conflict_do_update(
                constraint="uq_lease_conversation",
                set_={
                    "fencing_token": fencing_token,
                    "worker_id": self._worker_id,
                    "acquired_at": now,
                    "expires_at": expires_at,
                    "released_at": None,
                },
                where=ConversationRunLease.expires_at <= now,  # Only overtake expired leases
            )
        )
        result = await self._session.execute(stmt_insert)
        if result.rowcount == 0:  # type: ignore[attr-defined]
            raise LeaseConflictError(
                f"Conversation {conversation_id!r} lease acquisition failed"
                " — another worker holds it",
                conversation_id=conversation_id,
            )
        return fencing_token

    async def release(self, tenant_id: uuid.UUID, conversation_id: str, fencing_token: str) -> None:
        """Release a conversation lease.

        Only releases the lease if the fencing token matches (prevents stale
        workers from releasing another worker's lease).

        Args:
            tenant_id: The tenant UUID.
            conversation_id: The conversation to unlock.
            fencing_token: The fencing token from acquire().
        """
        stmt = (
            update(ConversationRunLease)
            .where(
                ConversationRunLease.tenant_id == tenant_id,
                ConversationRunLease.conversation_id == conversation_id,
                ConversationRunLease.fencing_token == fencing_token,
            )
            .values(released_at=datetime.now(UTC))
        )
        await self._session.execute(stmt)

    async def is_valid(
        self, tenant_id: uuid.UUID, conversation_id: str, fencing_token: str
    ) -> bool:
        """Check if a fencing token is still valid.

        Args:
            tenant_id: The tenant UUID.
            conversation_id: The conversation.
            fencing_token: The fencing token to validate.

        Returns:
            bool: True if the fencing token is valid and not expired.
        """
        now = datetime.now(UTC)
        stmt = select(ConversationRunLease.id).where(
            ConversationRunLease.tenant_id == tenant_id,
            ConversationRunLease.conversation_id == conversation_id,
            ConversationRunLease.fencing_token == fencing_token,
            ConversationRunLease.expires_at > now,
            ConversationRunLease.released_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None
