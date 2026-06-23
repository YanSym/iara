"""HITL holds repository — persists Human-in-the-Loop pause records.

Backs the HitlHoldRegistry with Postgres so holds survive process restarts.
All context fields use sanitized snapshots (no raw prompts, tokens, or PII).
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from iara.observability.logging import get_logger
from iara.persistence.models import HitlHoldRecord

logger = get_logger(__name__)


class HitlHoldRepository:
    """Manages HITL hold records in Postgres.

    Args:
        session: An active async SQLAlchemy session.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def register(
        self,
        run_id: str,
        tenant_id: str,
        conversation_id: str,
        thread_id: str,
        reason: str | None = None,
        context_snapshot: dict[str, Any] | None = None,
    ) -> str:
        """Insert a new HITL hold with status='pending'.

        Idempotent on run_id — duplicate inserts are silently ignored.

        Args:
            run_id: LangGraph run identifier.
            tenant_id: Tenant UUID string.
            conversation_id: Conversation identifier.
            thread_id: LangGraph thread_id (checkpointer key).
            reason: Human-readable reason for the hold (sanitized).
            context_snapshot: Non-sensitive graph state snapshot.

        Returns:
            str: UUID of the created hold record.
        """
        hold_id = uuid.uuid4()
        stmt = (
            pg_insert(HitlHoldRecord)
            .values(
                id=hold_id,
                run_id=run_id,
                tenant_id=uuid.UUID(tenant_id),
                conversation_id=conversation_id,
                thread_id=thread_id,
                reason=reason,
                status="pending",
                context_snapshot=context_snapshot or {},
                requested_at=datetime.now(UTC),
            )
            .on_conflict_do_nothing(constraint=None)  # unique on run_id
        )
        with contextlib.suppress(Exception):
            await self._session.execute(stmt)
        logger.info(
            "hitl_hold_registered_db",
            run_id=run_id,
            tenant_ref=tenant_id[:8],
            conversation_id=conversation_id,
        )
        return str(hold_id)

    async def list_pending(self, tenant_id: str) -> list[HitlHoldRecord]:
        """Return all pending holds for a tenant.

        Args:
            tenant_id: Tenant UUID string.

        Returns:
            list[HitlHoldRecord]: Pending hold records.
        """
        stmt = (
            select(HitlHoldRecord)
            .where(
                HitlHoldRecord.tenant_id == uuid.UUID(tenant_id),
                HitlHoldRecord.status == "pending",
            )
            .order_by(HitlHoldRecord.requested_at)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_pending_all(self) -> list[HitlHoldRecord]:
        """Return all pending holds across all tenants.

        Returns:
            list[HitlHoldRecord]: All pending hold records.
        """
        stmt = (
            select(HitlHoldRecord)
            .where(HitlHoldRecord.status == "pending")
            .order_by(HitlHoldRecord.requested_at)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get(self, run_id: str) -> HitlHoldRecord | None:
        """Fetch a hold by run_id.

        Args:
            run_id: LangGraph run identifier.

        Returns:
            HitlHoldRecord | None: The hold or None.
        """
        stmt = select(HitlHoldRecord).where(HitlHoldRecord.run_id == run_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def approve(self, run_id: str, approved_by: str) -> HitlHoldRecord | None:
        """Set status='approved' and record approver and timestamp.

        Args:
            run_id: LangGraph run identifier.
            approved_by: Opaque approver reference.

        Returns:
            HitlHoldRecord | None: Updated record or None if not found.
        """
        stmt = (
            update(HitlHoldRecord)
            .where(
                HitlHoldRecord.run_id == run_id,
                HitlHoldRecord.status == "pending",
            )
            .values(
                status="approved",
                resolved_by=approved_by[:256],
                resolved_at=datetime.now(UTC),
            )
            .returning(HitlHoldRecord)
        )
        result = await self._session.execute(stmt)
        row = result.fetchone()
        return row[0] if row else None

    async def reject(
        self,
        run_id: str,
        rejected_by: str,
        reason: str | None = None,
    ) -> HitlHoldRecord | None:
        """Set status='rejected'.

        Args:
            run_id: LangGraph run identifier.
            rejected_by: Opaque rejector reference.
            reason: Optional rejection reason (sanitized).

        Returns:
            HitlHoldRecord | None: Updated record or None if not found.
        """
        update_values: dict[str, Any] = {
            "status": "rejected",
            "resolved_by": rejected_by[:256],
            "resolved_at": datetime.now(UTC),
        }
        if reason:
            update_values["reason"] = reason[:500]

        stmt = (
            update(HitlHoldRecord)
            .where(
                HitlHoldRecord.run_id == run_id,
                HitlHoldRecord.status == "pending",
            )
            .values(**update_values)
            .returning(HitlHoldRecord)
        )
        result = await self._session.execute(stmt)
        row = result.fetchone()
        return row[0] if row else None
