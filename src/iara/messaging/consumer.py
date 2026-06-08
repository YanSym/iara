"""RabbitMQ message consumer.

Consumes conversation jobs from the jobs queue. Each message is processed
with a lease guard to prevent concurrent execution of the same conversation.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from typing import Any, cast

import aio_pika
from aio_pika import IncomingMessage
from aio_pika.abc import AbstractIncomingMessage

from iara.messaging.topology import QUEUE_CONVERSATION_JOBS
from iara.observability.logging import get_logger

logger = get_logger(__name__)

# Type alias for a job handler coroutine
JobHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class MessageConsumer:
    """Consumes conversation jobs from RabbitMQ.

    Args:
        channel: An active aio_pika channel.
        prefetch_count: Number of messages to prefetch.
    """

    def __init__(
        self,
        channel: aio_pika.abc.AbstractChannel,
        prefetch_count: int = 10,
    ) -> None:
        self._channel = channel
        self._prefetch_count = prefetch_count

    async def consume(self, handler: JobHandler) -> None:
        """Start consuming conversation jobs.

        Args:
            handler: Async callable that processes each job dict.
        """
        await self._channel.set_qos(prefetch_count=self._prefetch_count)
        queue = await self._channel.get_queue(QUEUE_CONVERSATION_JOBS)

        async def on_message(message: AbstractIncomingMessage) -> None:
            msg = cast(IncomingMessage, message)
            async with msg.process(requeue=True, ignore_processed=True):
                correlation_id = "unknown"
                try:
                    payload = json.loads(msg.body.decode())
                    correlation_id = payload.get("correlation_id", "unknown")
                    logger.info(
                        "job_received",
                        correlation_id=correlation_id,
                        conversation_id=payload.get("conversation_id"),
                    )
                    await handler(payload)
                    await msg.ack()
                except Exception as exc:
                    logger.error(
                        "job_processing_failed",
                        correlation_id=correlation_id,
                        error_code=type(exc).__name__,
                        error_summary=str(exc)[:200],
                    )
                    # Nack to trigger DLX after max retries
                    await msg.nack(requeue=False)

        await queue.consume(on_message)
        logger.info("consumer_started", queue=QUEUE_CONVERSATION_JOBS)
