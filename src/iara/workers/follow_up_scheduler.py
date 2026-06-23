"""Follow-up scheduler worker — delivers scheduled follow-up messages.

Polls ``follow_up_queue`` for items with ``trigger_at <= now`` and enqueues
each to ``provider_command_outbox`` via the chatwoot provider. Respects:

- Max-attempts: items exceeding ``max_attempts`` are marked skipped.
- Opt-out: opted-out items are marked skipped immediately.
- Idempotency: the outbox insert uses the follow-up's ``idempotency_key``.

Per INV-04: all side effects are gated through the outbox. This worker
writes to the outbox table; the OutboxDrainerWorker delivers the message.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from iara.config.settings import Settings
from iara.contracts.provider import ProviderCommand, RiskClass
from iara.observability.logging import get_logger
from iara.persistence.repositories.follow_up import FollowUpRepository
from iara.persistence.repositories.outbox import OutboxRepository

logger = get_logger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 30
DEFAULT_BATCH_SIZE = 50


class FollowUpSchedulerWorker:
    """Polls follow_up_queue and promotes due items to the outbox.

    Args:
        settings: Application settings.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    async def start(self, shutdown_event: asyncio.Event) -> None:
        """Start the scheduler poll loop until shutdown_event is set.

        Args:
            shutdown_event: Signals graceful shutdown.
        """
        engine = create_async_engine(
            self._settings.database_url,
            pool_size=3,
            max_overflow=0,
        )
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)

        logger.info(
            "follow_up_scheduler_ready",
            poll_interval=DEFAULT_POLL_INTERVAL_SECONDS,
        )

        while not shutdown_event.is_set():
            try:
                await self._process_batch()
            except Exception as exc:
                logger.error(
                    "follow_up_scheduler_error",
                    error_code=type(exc).__name__,
                    error_summary=str(exc)[:200],
                )

            await asyncio.sleep(DEFAULT_POLL_INTERVAL_SECONDS)

        await engine.dispose()
        logger.info("follow_up_scheduler_stopped")

    async def _process_batch(self) -> None:
        """Fetch due follow-ups and promote each to the outbox."""
        if self._session_factory is None:
            return

        now = datetime.now(UTC)

        async with self._session_factory() as session:
            follow_up_repo = FollowUpRepository(session)
            items = await follow_up_repo.fetch_due(now=now, batch_size=DEFAULT_BATCH_SIZE)

        if not items:
            return

        logger.info("follow_up_scheduler_batch", item_count=len(items))

        for item in items:
            await self._promote_item(item)

    async def _promote_item(self, item: Any) -> None:
        """Promote a single follow-up item to the provider_command_outbox.

        Skips items that are opted out or have exceeded max_attempts.

        Args:
            item: A FollowUpQueueItem ORM instance.
        """
        if self._session_factory is None:
            return

        item_id = str(item.id)
        conversation_id = str(item.conversation_id)
        tenant_id = str(item.tenant_id)

        log = logger.bind(
            item_id=item_id,
            conversation_id=conversation_id,
            tenant_ref=tenant_id[:8],
        )

        # Opt-out check
        if item.opted_out:
            log.info("follow_up_skipped_opted_out")
            async with self._session_factory() as session:
                repo = FollowUpRepository(session)
                await repo.mark_skipped(item_id, reason="opted_out")
                await session.commit()
            return

        # Max-attempts check
        attempt_count = int(item.attempt_count or 0)
        max_attempts = int(item.max_attempts or 3)
        if attempt_count >= max_attempts:
            log.info("follow_up_skipped_max_attempts", attempt_count=attempt_count)
            async with self._session_factory() as session:
                repo = FollowUpRepository(session)
                await repo.mark_skipped(item_id, reason="max_attempts")
                await session.commit()
            return

        # Increment attempt before enqueuing (fail-safe: don't loop on errors)
        async with self._session_factory() as session:
            repo = FollowUpRepository(session)
            new_count = await repo.increment_attempt(item_id)
            await session.commit()

        # Build outbox command
        message_ref = str(item.message_ref or "")
        idempotency_key = str(item.idempotency_key or "")
        correlation_id = str(item.correlation_id or "")

        # Derive a stable command_id from the item's idempotency_key so the
        # outbox insert is itself idempotent on retry.
        command_id = str(
            uuid.UUID(
                hashlib.sha256(f"followup:{idempotency_key}:{new_count}".encode()).hexdigest()[:32]
            )
        )

        provider_command = ProviderCommand(
            command_id=command_id,
            idempotency_key=f"{idempotency_key}:attempt:{new_count}",
            tenant_id=uuid.UUID(tenant_id),
            provider="chatwoot",
            account_id_ref="",
            capability_name="followup_reengage_conversation",
            parameters={
                "conversation_id": conversation_id,
                "message_ref": message_ref,
                "message_length": int(item.message_length or 0),
                "reason_ref": str(item.reason_ref or ""),
                "follow_up_item_id": item_id,
            },
            risk_class=RiskClass.LOW_WRITE,
            correlation_id=correlation_id,
            retry_count=0,
        )

        try:
            async with self._session_factory() as session:
                outbox_repo = OutboxRepository(session)
                await outbox_repo.enqueue(provider_command)
                await session.commit()

            # Mark sent only after the outbox write succeeds
            async with self._session_factory() as session:
                follow_up_repo = FollowUpRepository(session)
                await follow_up_repo.mark_sent(item_id)
                await session.commit()

            log.info("follow_up_promoted_to_outbox", command_id=command_id)

        except Exception as exc:
            log.warning(
                "follow_up_promote_failed",
                error_code=type(exc).__name__,
                error_summary=str(exc)[:200],
                attempt_count=new_count,
            )
            if new_count >= max_attempts:
                async with self._session_factory() as session:
                    follow_up_repo = FollowUpRepository(session)
                    await follow_up_repo.mark_failed(item_id, error=type(exc).__name__)
                    await session.commit()
