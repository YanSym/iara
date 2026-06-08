"""Worker entrypoint — starts the job consumer and outbox drainer.

Run with: python -m iara.workers.main
Or via Makefile: make worker
"""

from __future__ import annotations

import asyncio
import signal

from iara.config.settings import get_settings
from iara.observability.logging import configure_logging, get_logger
from iara.workers.job_consumer import JobConsumerWorker
from iara.workers.outbox_drainer import OutboxDrainerWorker

logger = get_logger(__name__)


async def main() -> None:
    """Main worker loop.

    Starts:
    1. RabbitMQ job consumer (conversation processing jobs)
    2. Outbox drainer (pending provider commands)
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

    job_consumer = JobConsumerWorker(settings=settings)
    outbox_drainer = OutboxDrainerWorker(settings=settings)

    tasks = [
        asyncio.create_task(
            job_consumer.start(shutdown_event),
            name="job_consumer",
        ),
        asyncio.create_task(
            outbox_drainer.start(shutdown_event),
            name="outbox_drainer",
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
