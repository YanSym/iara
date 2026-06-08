"""RabbitMQ topology declaration.

Defines exchanges, queues, dead-letter exchanges (DLX), and bindings.
The topology is declared at startup by calling ``declare_topology()``.

Topology overview:
- ``iara.jobs`` exchange (topic) → ``iara.jobs.conversation`` queue
- ``iara.dlx`` exchange (fanout) → ``iara.jobs.dead`` queue
- Retry uses message TTL + DLX to implement exponential backoff

Queue naming: ``iara.{purpose}.{scope}``
Exchange naming: ``iara.{purpose}``
"""

from __future__ import annotations

import aio_pika
from aio_pika import ExchangeType

from iara.observability.logging import get_logger

logger = get_logger(__name__)

# ── Exchange names ─────────────────────────────────────────────────────────────

EXCHANGE_JOBS = "iara.jobs"
EXCHANGE_DLX = "iara.dlx"
EXCHANGE_RETRY = "iara.retry"

# ── Queue names ────────────────────────────────────────────────────────────────

QUEUE_CONVERSATION_JOBS = "iara.jobs.conversation"
QUEUE_DEAD_LETTERS = "iara.jobs.dead"
QUEUE_RETRY_DELAY = "iara.jobs.retry.delay"

# ── Routing keys ───────────────────────────────────────────────────────────────

ROUTING_CONVERSATION_JOB = "job.conversation"


class RabbitMQTopology:
    """Declares the complete RabbitMQ topology.

    Args:
        channel: An active aio_pika channel.
    """

    def __init__(self, channel: aio_pika.abc.AbstractChannel) -> None:
        self._channel = channel

    async def declare(self) -> None:
        """Declare all exchanges, queues, and bindings.

        This is idempotent — safe to call on every startup.
        """
        # Dead-letter exchange
        dlx = await self._channel.declare_exchange(
            EXCHANGE_DLX,
            ExchangeType.FANOUT,
            durable=True,
        )

        # Main jobs exchange
        jobs_exchange = await self._channel.declare_exchange(
            EXCHANGE_JOBS,
            ExchangeType.TOPIC,
            durable=True,
        )

        # Retry exchange (messages with TTL that re-route back to main queue)
        retry_exchange = await self._channel.declare_exchange(
            EXCHANGE_RETRY,
            ExchangeType.TOPIC,
            durable=True,
        )

        # Dead-letter queue
        dead_queue = await self._channel.declare_queue(
            QUEUE_DEAD_LETTERS,
            durable=True,
        )
        await dead_queue.bind(dlx)

        # Retry delay queue (TTL=30s, then routes to jobs exchange)
        retry_queue = await self._channel.declare_queue(
            QUEUE_RETRY_DELAY,
            durable=True,
            arguments={
                "x-message-ttl": 30_000,  # 30 seconds
                "x-dead-letter-exchange": EXCHANGE_JOBS,
            },
        )
        await retry_queue.bind(retry_exchange, routing_key="#")

        # Main conversation jobs queue
        jobs_queue = await self._channel.declare_queue(
            QUEUE_CONVERSATION_JOBS,
            durable=True,
            arguments={
                "x-dead-letter-exchange": EXCHANGE_DLX,
            },
        )
        await jobs_queue.bind(jobs_exchange, routing_key=ROUTING_CONVERSATION_JOB)

        logger.info(
            "rabbitmq_topology_declared",
            exchanges=[EXCHANGE_JOBS, EXCHANGE_DLX, EXCHANGE_RETRY],
            queues=[QUEUE_CONVERSATION_JOBS, QUEUE_DEAD_LETTERS, QUEUE_RETRY_DELAY],
        )


async def declare_topology(channel: aio_pika.abc.AbstractChannel) -> None:
    """Convenience function to declare the full RabbitMQ topology.

    Args:
        channel: An active aio_pika channel.
    """
    topology = RabbitMQTopology(channel)
    await topology.declare()
