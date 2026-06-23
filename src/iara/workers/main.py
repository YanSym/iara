"""Worker entrypoint — starts the job consumer and outbox drainer.

Run with: python -m iara.workers.main
Or via Makefile: make worker
"""

from __future__ import annotations

import asyncio
import signal

from iara.config.settings import get_settings
from iara.observability.logging import configure_logging, get_logger
from iara.workers.follow_up_scheduler import FollowUpSchedulerWorker
from iara.workers.job_consumer import JobConsumerWorker
from iara.workers.outbox_drainer import OutboxDrainerWorker

logger = get_logger(__name__)


async def main() -> None:
    """Main worker loop.

    Starts:
    1. RabbitMQ job consumer (conversation processing jobs — uses Postgres checkpointer)
    2. Outbox drainer (pending provider commands — multi-provider routing)
    3. Follow-up scheduler (promotes due follow_up_queue items to the outbox)
    """
    settings = get_settings()
    configure_logging(level=settings.log_level, log_format=settings.log_format)

    logger.info(
        "worker_starting",
        env=settings.iara_env,
        rabbitmq_url_prefix=settings.rabbitmq_url[:20],
    )

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def handle_shutdown() -> None:
        logger.info("worker_shutdown_signal_received")
        loop.call_soon_threadsafe(shutdown_event.set)

    loop.add_signal_handler(signal.SIGTERM, handle_shutdown)
    loop.add_signal_handler(signal.SIGINT, handle_shutdown)

    # Build provider adapters for outbox routing: chatwoot / google_calendar / clinicorp.
    from iara.provider.chatwoot.mcp_adapter import ChatwootMcpAdapter
    from iara.provider.chatwoot.mcp_registry import ChatwootMcpRegistry

    chatwoot_adapter = ChatwootMcpAdapter(
        registry=ChatwootMcpRegistry(),
        mcp_base_url=settings.chatwoot_mcp_base_url,
        account_id=settings.chatwoot_account_id,
        mcp_slug=settings.chatwoot_mcp_slug,
        credential_ref=settings.chatwoot_mcp_credential_ref,
        timeout_seconds=settings.chatwoot_mcp_timeout_seconds,
        max_retries=settings.chatwoot_mcp_max_retries,
    )

    from iara.provider.scheduling.factory import (
        build_clinicorp_write_adapter,
        build_google_calendar_write_adapter,
    )

    adapters = {
        "chatwoot": chatwoot_adapter,
        "google_calendar": build_google_calendar_write_adapter(settings),
        "clinicorp": build_clinicorp_write_adapter(settings),
    }

    job_consumer = JobConsumerWorker(settings=settings)
    outbox_drainer = OutboxDrainerWorker(settings=settings, adapters=adapters)
    follow_up_scheduler = FollowUpSchedulerWorker(settings=settings)

    tasks = [
        asyncio.create_task(
            job_consumer.start(shutdown_event),
            name="job_consumer",
        ),
        asyncio.create_task(
            outbox_drainer.start(shutdown_event),
            name="outbox_drainer",
        ),
        asyncio.create_task(
            follow_up_scheduler.start(shutdown_event),
            name="follow_up_scheduler",
        ),
    ]

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)

    for task in done:
        if exc := task.exception():
            logger.error(
                "worker_task_crashed",
                task_name=task.get_name(),
                error_code=type(exc).__name__,
                error_summary=str(exc)[:200],
            )

    shutdown_event.set()
    for task in pending:
        task.cancel()

    await asyncio.gather(*pending, return_exceptions=True)
    logger.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(main())
