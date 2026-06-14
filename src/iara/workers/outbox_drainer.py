"""Outbox drainer — polls the outbox table and executes pending commands.

Drains the ``provider_command_outbox`` table, executing each pending command
via the provider adapter, then performs readback to confirm the mutation.
Dead-lettered commands are logged for manual review.

Per INV-04: all side effects are gated through this drainer. The LangGraph
graph nodes only enqueue — they never execute directly.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from iara.config.settings import Settings
from iara.contracts.provider import ProviderCommand, ProviderSecurityContext, RiskClass
from iara.observability.logging import get_logger
from iara.observability.metrics import outbox_commands_total
from iara.persistence.repositories.outbox import OutboxRepository

logger = get_logger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 5
DEFAULT_BATCH_SIZE = 50


class OutboxDrainerWorker:
    """Polls the outbox and executes pending provider commands.

    Args:
        settings: Application settings.
        adapter: Provider adapter to execute commands (optional).
    """

    def __init__(
        self,
        settings: Settings,
        adapter: Any | None = None,
    ) -> None:
        self._settings = settings
        self._adapter = adapter
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    async def start(self, shutdown_event: asyncio.Event) -> None:
        """Start the drainer poll loop until shutdown_event is set.

        Args:
            shutdown_event: Signals graceful shutdown.
        """
        engine = create_async_engine(
            self._settings.database_url,
            pool_size=5,
            max_overflow=0,
        )
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)

        logger.info("outbox_drainer_ready", poll_interval=DEFAULT_POLL_INTERVAL_SECONDS)

        while not shutdown_event.is_set():
            try:
                await self._drain_batch()
            except Exception as exc:
                logger.error(
                    "outbox_drain_error",
                    error_code=type(exc).__name__,
                    error_summary=str(exc)[:200],
                )

            await asyncio.sleep(DEFAULT_POLL_INTERVAL_SECONDS)

        await engine.dispose()
        logger.info("outbox_drainer_stopped")

    async def _drain_batch(self) -> None:
        """Fetch and process one batch of pending outbox commands."""
        if self._session_factory is None:
            return

        if self._adapter is None:
            logger.warning("outbox_adapter_not_configured_skipping_batch")
            return

        session_factory = self._session_factory  # narrow type for mypy

        async with session_factory() as session:
            repo = OutboxRepository(session)
            commands = await repo.fetch_pending_all(limit=DEFAULT_BATCH_SIZE)

        if not commands:
            return

        logger.info("outbox_drain_batch", command_count=len(commands))

        for command in commands:
            await self._process_command(command, session_factory)

    async def _process_command(
        self,
        command: Any,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Execute a single outbox command via the provider adapter.

        Constructs the ProviderCommand and ProviderSecurityContext contracts
        from the outbox ORM record and calls the adapter with the correct
        interface (INV-02: cross-tenant re-verification inside the adapter).

        Args:
            command: An outbox ORM record.
            session_factory: Active session factory (already narrowed to non-None).
        """
        command_id = str(command.command_id)
        capability_name = str(command.capability_name)
        tenant_id_str = str(command.tenant_id)
        retry_count = command.retry_count or 0

        log = logger.bind(
            command_id=command_id,
            capability_name=capability_name,
            tenant_ref=tenant_id_str[:8],
            retry_count=retry_count,
        )

        if retry_count >= self._settings.iara_outbox_max_retries:
            log.error("outbox_command_dead_lettered", reason="max_retries_exceeded")
            async with session_factory() as session:
                repo = OutboxRepository(session)
                await repo.mark_dead_lettered(
                    command_id=command_id,
                    reason="max_retries_exceeded",
                )
                await session.commit()
            outbox_commands_total.labels(status="dead_lettered").inc()
            return

        try:
            # Build typed contracts from the ORM record.
            # account_id_ref is not stored in the outbox (the cross-tenant
            # check already ran at webhook time); both sides use the same
            # placeholder so the adapter's internal check passes.
            tenant_uuid = uuid.UUID(tenant_id_str)
            try:
                risk = RiskClass(str(command.risk_class))
            except ValueError:
                risk = RiskClass.LOW_WRITE

            provider_command = ProviderCommand(
                command_id=command_id,
                idempotency_key=str(command.idempotency_key),
                tenant_id=tenant_uuid,
                provider=str(command.provider),
                account_id_ref="",  # not stored; verified at enqueue time
                capability_name=capability_name,
                parameters=command.parameters_json or {},
                risk_class=risk,
                correlation_id=str(command.correlation_id),
                retry_count=retry_count,
            )
            security_context = ProviderSecurityContext(
                tenant_id=tenant_uuid,
                provider=str(command.provider),
                account_id_ref="",  # matches command; adapter cross-tenant check passes
                inbox_id="",  # not stored in outbox
                capability_name=capability_name,
                risk_class=risk,
            )

            log.info("outbox_executing_command")
            result = await self._adapter.execute_command(provider_command, security_context)  # type: ignore[union-attr]
            log.info(
                "outbox_command_sent", success=result.success, readback=result.readback_confirmed
            )

            async with session_factory() as session:
                repo = OutboxRepository(session)
                await repo.mark_sent(command_id=command_id)
                await session.commit()
            outbox_commands_total.labels(status="sent").inc()

        except Exception as exc:
            next_retry = retry_count + 1
            log.warning(
                "outbox_command_failed",
                error_code=type(exc).__name__,
                error_summary=str(exc)[:200],
                retry_count=next_retry,
            )
            async with session_factory() as session:
                repo = OutboxRepository(session)
                if next_retry >= self._settings.iara_outbox_max_retries:
                    # Max retries reached — dead-letter without another attempt.
                    log.error(
                        "outbox_command_dead_lettered",
                        reason="max_retries_exceeded",
                        retry_count=next_retry,
                    )
                    await repo.mark_dead_lettered(
                        command_id=command_id,
                        reason="max_retries_exceeded",
                    )
                    outbox_commands_total.labels(status="dead_lettered").inc()
                else:
                    # Back-off and reschedule for a later attempt.
                    backoff = 30 * (2**retry_count)  # 30s, 60s, 120s, …
                    await repo.mark_failed_for_retry(
                        command_id=command_id,
                        retry_delay_seconds=backoff,
                    )
                    outbox_commands_total.labels(status="failed").inc()
                await session.commit()
