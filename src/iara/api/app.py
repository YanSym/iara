"""FastAPI application factory.

Creates and configures the IAra webhook server with all routers,
lifespan management, and CORS configuration.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from iara.api.routers.admin import router as admin_router
from iara.api.routers.chat import router as chat_router
from iara.api.routers.hitl import router as hitl_router
from iara.api.routers.webhooks import router as webhook_router
from iara.config.settings import get_settings
from iara.observability.logging import configure_logging, get_logger
from iara.observability.metrics import CONTENT_TYPE_LATEST, generate_latest

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None]:
    """Manage application lifespan — startup and shutdown.

    Args:
        application: The FastAPI application instance.

    Yields:
        None: Control is yielded during the application's running period.
    """
    settings = get_settings()
    configure_logging(level=settings.log_level, log_format=settings.log_format)

    logger.info(
        "iara_starting",
        env=settings.iara_env,
        version="0.1.0",
    )

    # Connect to RabbitMQ (optional — graceful degradation if not running)
    try:
        import aio_pika

        application.state.rabbitmq = await aio_pika.connect_robust(
            settings.rabbitmq_url,
            timeout=5,
        )
        logger.info("rabbitmq_connected")
    except Exception as exc:
        application.state.rabbitmq = None
        logger.warning(
            "rabbitmq_unavailable_webhook_jobs_disabled",
            error_summary=str(exc)[:200],
        )

    yield

    # Shutdown: close connections
    if rabbitmq := getattr(application.state, "rabbitmq", None):
        await rabbitmq.close()
        logger.info("rabbitmq_disconnected")

    logger.info("iara_stopping")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        FastAPI: Configured FastAPI application.
    """
    settings = get_settings()

    app = FastAPI(
        title="IAra Runtime",
        description="Multi-tenant conversational agent runtime",
        version="0.1.0",
        docs_url="/docs" if settings.is_development else None,
        redoc_url="/redoc" if settings.is_development else None,
        openapi_url="/openapi.json" if settings.is_development else None,
        lifespan=lifespan,
    )

    # CORS
    if settings.iara_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.iara_allowed_origins,
            allow_credentials=False,
            allow_methods=["POST", "GET"],
            allow_headers=["Content-Type", "X-Request-ID"],
        )

    # Routers
    app.include_router(webhook_router, prefix="/webhooks")
    app.include_router(chat_router, prefix="/chat")
    app.include_router(admin_router, prefix="/admin")
    app.include_router(hitl_router, prefix="/hitl")

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok", "service": "iara-runtime"}

    @app.get("/ready")
    async def readiness() -> dict[str, str]:
        """Readiness probe endpoint."""
        return {"status": "ready"}

    @app.get("/live")
    async def liveness() -> dict[str, str]:
        """Liveness probe endpoint — confirms the process is alive."""
        return {"status": "alive"}

    @app.get("/metrics")
    async def metrics() -> Response:
        """Prometheus metrics endpoint."""
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


# Application singleton — used by uvicorn
app = create_app()
