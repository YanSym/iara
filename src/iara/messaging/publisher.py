"""RabbitMQ message publisher.

Publishes conversation job messages to the jobs exchange. All messages are
persistent (durable delivery mode 2) to survive broker restarts.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import aio_pika
from aio_pika import DeliveryMode, Message

from iara.messaging.topology import EXCHANGE_JOBS, ROUTING_CONVERSATION_JOB
from iara.observability.logging import get_logger

logger = get_logger(__name__)


class ConversationJob:
    """A conversation processing job to be queued.

    Attributes:
        tenant_id: Tenant UUID string.
        conversation_id: Conversation identifier.
        correlation_id: Distributed tracing ID.
        idempotency_key: Deduplication key.
        event_ref: Hash reference to the triggering event.
        scheduled_at: ISO 8601 timestamp when the job was created.
    """

    def __init__(
        self,
        tenant_id: str,
        conversation_id: str,
        correlation_id: str,
        idempotency_key: str,
        event_ref: str,
        content: str | None = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.conversation_id = conversation_id
        self.correlation_id = correlation_id
        self.idempotency_key = idempotency_key
        self.event_ref = event_ref
        self.content = content
        self.scheduled_at = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary.

        Returns:
            dict[str, Any]: Serialized job.
        """
        return {
            "tenant_id": self.tenant_id,
            "conversation_id": self.conversation_id,
            "correlation_id": self.correlation_id,
            "idempotency_key": self.idempotency_key,
            "event_ref": self.event_ref,
            "content": self.content,
            "scheduled_at": self.scheduled_at,
        }


class MessagePublisher:
    """Publishes messages to RabbitMQ.

    Args:
        channel: An active aio_pika channel.
    """

    def __init__(self, channel: aio_pika.abc.AbstractChannel) -> None:
        self._channel = channel

    async def publish_conversation_job(self, job: ConversationJob) -> None:
        """Publish a conversation processing job.

        Args:
            job: The job to publish.
        """
        exchange = await self._channel.get_exchange(EXCHANGE_JOBS)
        body = json.dumps(job.to_dict()).encode()
        message = Message(
            body=body,
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
            headers={
                "correlation_id": job.correlation_id,
                "tenant_id": job.tenant_id,
            },
        )
        await exchange.publish(message, routing_key=ROUTING_CONVERSATION_JOB)
        logger.info(
            "job_published",
            correlation_id=job.correlation_id,
            conversation_id=job.conversation_id,
            tenant_ref=job.tenant_id[:8],
        )
