"""Worker job consumer — processes conversation jobs from RabbitMQ.

Each job acquires a conversation-level lease (fencing token), runs the
LangGraph conversational graph, and releases the lease on completion.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import aio_pika

from iara.config.settings import Settings
from iara.messaging.consumer import MessageConsumer
from iara.messaging.topology import declare_topology
from iara.observability.logging import get_logger

logger = get_logger(__name__)


class JobConsumerWorker:
    """Consumes conversation processing jobs and orchestrates the LangGraph.

    Args:
        settings: Application settings.
        graph: Compiled LangGraph graph (optional; built on start if None).
    """

    def __init__(
        self,
        settings: Settings,
        graph: Any | None = None,
    ) -> None:
        self._settings = settings
        self._graph = graph
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None

    async def start(self, shutdown_event: asyncio.Event) -> None:
        """Start consuming jobs until shutdown_event is set.

        When no graph was pre-injected, builds the production graph using a
        Postgres checkpointer (falls back to MemorySaver if the package is
        unavailable or the DB is unreachable).

        Args:
            shutdown_event: Signals graceful shutdown.
        """
        if self._graph is None:
            await self._build_graph_with_checkpointer(shutdown_event)
            return

        await self._run_consumer(shutdown_event)

    async def _build_graph_with_checkpointer(self, shutdown_event: asyncio.Event) -> None:
        """Build the production graph with a Postgres checkpointer and run.

        The checkpointer connection pool is kept alive for the entire duration
        of the worker via the async context manager.
        """
        from iara.graph.builder import build_production_graph

        try:
            from iara.persistence.checkpointer import postgres_checkpointer

            async with postgres_checkpointer(self._settings.database_url) as checkpointer:
                self._graph = build_production_graph(self._settings, checkpointer=checkpointer)
                await self._run_consumer(shutdown_event)
        except ImportError:
            logger.warning(
                "postgres_checkpointer_unavailable_using_memory",
                reason="langgraph-checkpoint-postgres not installed",
            )
            self._graph = build_production_graph(self._settings)
            await self._run_consumer(shutdown_event)
        except Exception as exc:
            logger.warning(
                "postgres_checkpointer_failed_using_memory",
                error_code=type(exc).__name__,
                error_summary=str(exc)[:200],
            )
            self._graph = build_production_graph(self._settings)
            await self._run_consumer(shutdown_event)

    async def _run_consumer(self, shutdown_event: asyncio.Event) -> None:
        """Run the RabbitMQ consumer loop.

        Args:
            shutdown_event: Signals graceful shutdown.
        """

        self._connection = await aio_pika.connect_robust(self._settings.rabbitmq_url)

        async with self._connection:
            channel = await self._connection.channel()
            await declare_topology(channel)

            consumer = MessageConsumer(
                channel=channel,
                prefetch_count=self._settings.rabbitmq_prefetch_count,
            )

            await consumer.consume(self._handle_job)
            logger.info("job_consumer_ready")

            await shutdown_event.wait()
            logger.info("job_consumer_shutdown_requested")

    async def _handle_job(self, payload: dict[str, Any]) -> None:
        """Process a single conversation job.

        Args:
            payload: Job payload from the queue.
        """
        tenant_id = payload.get("tenant_id", "")
        conversation_id = payload.get("conversation_id", "")
        correlation_id = payload.get("correlation_id", str(uuid.uuid4()))
        idempotency_key = payload.get("idempotency_key", "")
        content = payload.get("content") or ""
        attachments = payload.get("attachments") or []
        sender_type = payload.get("sender_type", "contact")
        sender_ref = payload.get("sender_ref", "")

        log = logger.bind(
            tenant_ref=tenant_id[:8] if tenant_id else "unknown",
            conversation_id=conversation_id,
            correlation_id=correlation_id,
        )

        if not tenant_id or not conversation_id:
            log.error(
                "job_missing_required_fields",
                has_tenant_id=bool(tenant_id),
                has_conversation_id=bool(conversation_id),
            )
            return

        log.info("job_processing_start")

        # Graph is guaranteed to be set — start() always builds it before consuming.
        assert self._graph is not None, "graph must be set before _handle_job is called"

        try:
            config = {
                "configurable": {
                    "thread_id": f"{tenant_id}:{conversation_id}",
                },
            }

            messages = []
            if content:
                messages = [{"role": "user", "content": content}]

            initial_state = {
                "run_id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "correlation_id": correlation_id,
                "eligibility_status": "pending",
                "media_processed": False,
                "context_built": False,
                "messages": messages,
                "metadata": {
                    "idempotency_key": idempotency_key,
                    "event_ref": payload.get("event_ref", ""),
                    "attachments": attachments,
                    "sender_type": sender_type,
                    "sender_ref": sender_ref,
                },
            }

            result = await self._graph.ainvoke(initial_state, config=config)

            log.info(
                "job_processing_complete",
                eligibility_status=result.get("eligibility_status"),
                response_sent=result.get("response_sent"),
                step_count=result.get("step_count", 0),
            )

        except Exception as exc:
            log.error(
                "job_processing_error",
                error_code=type(exc).__name__,
                error_summary=str(exc)[:200],
            )
            raise
