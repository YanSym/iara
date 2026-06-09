"""OutboxService — bridges graph nodes to OutboxRepository.

Provides async methods for enqueueing provider commands and tool commands,
managing sessions on demand so the service is safe to share across async
contexts without holding a long-lived session.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iara.contracts.provider import ProviderCommand, RiskClass
from iara.observability.logging import get_logger
from iara.persistence.repositories.outbox import OutboxRepository

logger = get_logger(__name__)


class OutboxService:
    """Thin service layer over OutboxRepository for injection into graph nodes.

    Args:
        session_factory: Async SQLAlchemy session factory.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def enqueue(
        self,
        command_id: str,
        tenant_id: str,
        conversation_id: str,
        capability_name: str,
        parameters: dict[str, Any],
        correlation_id: str,
        idempotency_key: str,
    ) -> None:
        """Enqueue a provider command (called by command_dispatch_node).

        Args:
            command_id: Unique command identifier.
            tenant_id: Tenant UUID string.
            conversation_id: Conversation identifier (for logging only).
            capability_name: The capability to invoke (e.g. 'send_message').
            parameters: Command parameters.
            correlation_id: Distributed tracing ID.
            idempotency_key: Deduplication key.
        """
        command = ProviderCommand(
            command_id=command_id,
            idempotency_key=idempotency_key,
            tenant_id=uuid.UUID(tenant_id),
            provider="chatwoot",
            account_id_ref="",
            capability_name=capability_name,
            parameters=parameters,
            risk_class=RiskClass.LOW_WRITE,
            correlation_id=correlation_id,
        )
        async with self._session_factory() as session:
            repo = OutboxRepository(session)
            await repo.enqueue(command)
            await session.commit()

        logger.info(
            "outbox_service_enqueued",
            capability=capability_name,
            command_id=command_id,
        )

    async def enqueue_tool_command(
        self,
        command_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        tenant_id: str,
        idempotency_key: str,
        correlation_id: str,
    ) -> None:
        """Enqueue a tool-originated side-effect command (called by ToolExecutor).

        Args:
            command_id: Unique command identifier.
            tool_name: Logical tool name (maps to capability_name in outbox).
            arguments: Tool arguments to pass as command parameters.
            tenant_id: Tenant UUID string.
            idempotency_key: Deduplication key.
            correlation_id: Distributed tracing ID.
        """
        command = ProviderCommand(
            command_id=command_id,
            idempotency_key=idempotency_key,
            tenant_id=uuid.UUID(tenant_id),
            provider="chatwoot",
            account_id_ref="",
            capability_name=tool_name,
            parameters=arguments,
            risk_class=RiskClass.LOW_WRITE,
            correlation_id=correlation_id,
        )
        async with self._session_factory() as session:
            repo = OutboxRepository(session)
            await repo.enqueue(command)
            await session.commit()

        logger.info(
            "outbox_service_tool_enqueued",
            tool=tool_name,
            command_id=command_id,
        )
