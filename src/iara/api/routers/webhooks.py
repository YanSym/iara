"""Webhook router — receives Chatwoot webhook events.

This router implements the webhook entry point:
  POST /webhooks/chatwoot/{tenant_key}

Per the architecture:
1. Auth/tenant/account/inbox/source_channel validated.
2. Raw payload referenced by hash only — never stored.
3. Pydantic normalizes to NormalizedChatwootEvent.
4. EligibilityDecision gates the event.
5. Idempotency and debounce registered in Postgres (when DB available).
6. Wakeup/job sent to RabbitMQ.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status

from iara.contracts.errors import FailClosedError
from iara.eligibility.decision import EligibilityChecker
from iara.eligibility.normalizer import ChatwootEventNormalizer
from iara.observability.logging import get_logger
from iara.observability.metrics import (
    webhook_request_duration_seconds,
    webhook_requests_total,
)
from iara.tenancy.resolver import InMemoryTenantRepository, TenantResolver

logger = get_logger(__name__)

router = APIRouter(tags=["webhooks"])

# Development/test resolver (replaced by DB-backed resolver in production)
_test_repository = InMemoryTenantRepository()
_dev_resolver = TenantResolver(repository=_test_repository, cache_ttl_seconds=60)

# Pre-register a dev tenant so local testing works without a real DB.
if os.getenv("IARA_ENV", "development") in ("development", "sandbox"):
    _test_repository.register(
        os.getenv("IARA_DEV_TENANT_KEY", "test_tenant_001"),
        {
            "tenant_id": os.getenv("IARA_DEV_TENANT_ID", "12345678-1234-5678-1234-567812345678"),
            "name": "Dev Tenant",
            "status": "sandbox",
            "provider": "chatwoot",
            "provider_account_id": os.getenv("IARA_DEV_ACCOUNT_ID", "11111"),
        },
    )


# ── Eligibility adapter helpers ───────────────────────────────────────────────


class _IdempotencyCheckerAdapter:
    """Bridges EligibilityChecker's protocol to IdempotencyRepository.

    Captures tenant_id and session at construction time so the EligibilityChecker
    can call is_duplicate(key) without knowing the repository signature.
    """

    def __init__(self, repo: Any, tenant_id_uuid: Any) -> None:
        self._repo = repo
        self._tenant_id = tenant_id_uuid

    async def is_duplicate(self, idempotency_key: str) -> bool:
        """Return True if this event was already processed."""
        return await self._repo.is_duplicate(self._tenant_id, idempotency_key)


class _DebounceCheckerAdapter:
    """Bridges EligibilityChecker's protocol to DebounceRepository."""

    def __init__(self, repo: Any, tenant_id_uuid: Any) -> None:
        self._repo = repo
        self._tenant_id = tenant_id_uuid

    async def is_debouncing(self, tenant_id: str, conversation_id: str) -> bool:
        """Return True if the conversation is in the debounce window."""
        return await self._repo.is_debouncing(self._tenant_id, conversation_id)


async def _get_db_session(request: Request) -> Any:
    """Return a DB session factory from app.state, or None if not available."""
    return getattr(request.app.state, "db_session_factory", None)


@router.post(
    "/chatwoot/{tenant_key}",
    status_code=status.HTTP_200_OK,
    summary="Receive Chatwoot webhook event",
)
async def receive_chatwoot_webhook(
    tenant_key: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Receive and process a Chatwoot webhook event.

    This endpoint:
    1. Reads the raw body bytes.
    2. Resolves the tenant (Postgres-backed in staging/production, in-memory in dev).
    3. Normalizes the event via Pydantic.
    4. Checks eligibility including idempotency and debounce.
    5. Records accepted event and sets debounce window.
    6. Queues a processing job.

    Args:
        tenant_key: The tenant key from the URL path.
        request: The incoming HTTP request.
        background_tasks: FastAPI background tasks.

    Returns:
        dict[str, str]: Acknowledgment response.

    Raises:
        HTTPException: If the tenant is not found or the event is invalid.
    """
    correlation_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    _t_start = time.monotonic()

    try:
        raw_bytes = await request.body()
    except Exception as exc:
        webhook_requests_total.labels(status="error").inc()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to read request body",
        ) from exc

    if not raw_bytes:
        webhook_requests_total.labels(status="accepted").inc()
        webhook_request_duration_seconds.observe(time.monotonic() - _t_start)
        return {"status": "accepted", "correlation_id": correlation_id}

    # --- Tenant resolution ---
    # Use Postgres-backed resolver in staging/production; in-memory in dev/test.
    iara_env = os.getenv("IARA_ENV", "development")
    session_factory = getattr(request.app.state, "db_session_factory", None)

    if iara_env not in ("development", "sandbox") and session_factory is not None:
        from iara.tenancy.postgres_repository import PostgresTenantRepository

        async with session_factory() as db_session:
            pg_repo = PostgresTenantRepository(db_session)
            pg_resolver = TenantResolver(repository=pg_repo, cache_ttl_seconds=60)
            try:
                tenant_ctx = await pg_resolver.resolve(tenant_key)
            except FailClosedError as exc:
                logger.warning(
                    "webhook_tenant_resolution_failed",
                    tenant_key_prefix=tenant_key[:8] if tenant_key else "empty",
                    correlation_id=correlation_id,
                    error_code=exc.code,
                )
                webhook_requests_total.labels(status="error").inc()
                webhook_request_duration_seconds.observe(time.monotonic() - _t_start)
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Tenant not found",
                ) from exc
    else:
        try:
            tenant_ctx = await _dev_resolver.resolve(tenant_key)
        except FailClosedError as exc:
            logger.warning(
                "webhook_tenant_resolution_failed",
                tenant_key_prefix=tenant_key[:8] if tenant_key else "empty",
                correlation_id=correlation_id,
                error_code=exc.code,
            )
            webhook_requests_total.labels(status="error").inc()
            webhook_request_duration_seconds.observe(time.monotonic() - _t_start)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tenant not found",
            ) from exc

    # Parse payload
    try:
        payload = json.loads(raw_bytes)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload",
        ) from exc

    # Normalize event
    normalizer = ChatwootEventNormalizer(tenant_context=tenant_ctx)
    try:
        normalized = normalizer.normalize(
            raw_payload=payload,
            raw_bytes=raw_bytes,
            correlation_id=correlation_id,
        )
    except Exception as exc:
        logger.warning(
            "webhook_normalization_failed",
            correlation_id=correlation_id,
            error_code=type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Event normalization failed",
        ) from exc

    # --- Eligibility check with optional DB-backed idempotency + debounce ---
    idempotency_checker = None
    debounce_checker = None

    if session_factory is not None:
        try:

            from iara.config.settings import get_settings
            from iara.persistence.repositories.debounce import DebounceRepository
            from iara.persistence.repositories.idempotency import IdempotencyRepository

            settings = get_settings()
            tenant_uuid = tenant_ctx.tenant_id
            # Create a transient session for the eligibility check.
            # Both repos share the same session to avoid opening two connections.
            _check_session = session_factory()
            async with _check_session as _sess:
                idempotency_repo = IdempotencyRepository(_sess)
                debounce_repo = DebounceRepository(
                    _sess, debounce_seconds=settings.iara_debounce_seconds
                )
                idempotency_checker = _IdempotencyCheckerAdapter(idempotency_repo, tenant_uuid)
                debounce_checker = _DebounceCheckerAdapter(debounce_repo, tenant_uuid)

                checker = EligibilityChecker(
                    tenant_context=tenant_ctx,
                    idempotency_checker=idempotency_checker,
                    debounce_checker=debounce_checker,
                )
                decision = await checker.check(normalized)

                if decision.eligible:
                    # Record accepted event and set debounce window
                    await idempotency_repo.record(
                        tenant_id=tenant_uuid,
                        idempotency_key=normalized.idempotency_key,
                        raw_hash=normalized.raw_event_ref.raw_hash,
                        correlation_id=correlation_id,
                    )
                    await debounce_repo.set_debounce(
                        tenant_id=tenant_uuid,
                        conversation_id=normalized.conversation_id,
                    )
                    await _sess.commit()
        except Exception as exc:
            # DB failure must not block webhook processing — degrade gracefully
            logger.warning(
                "webhook_eligibility_db_failed_degrading",
                error_code=type(exc).__name__,
                error_summary=str(exc)[:200],
                correlation_id=correlation_id,
            )
            checker = EligibilityChecker(tenant_context=tenant_ctx)
            decision = await checker.check(normalized)
    else:
        checker = EligibilityChecker(tenant_context=tenant_ctx)
        decision = await checker.check(normalized)

    if not decision.eligible:
        logger.info(
            "webhook_event_rejected",
            correlation_id=correlation_id,
            reason=decision.reason,
        )
        webhook_requests_total.labels(status="rejected").inc()
        webhook_request_duration_seconds.observe(time.monotonic() - _t_start)
        return {
            "status": "rejected",
            "reason": decision.reason,
            "correlation_id": correlation_id,
        }

    # Extract transient attachment URLs from the raw payload for media processing.
    message_raw = payload.get("message", {}) or payload
    raw_attachments = message_raw.get("attachments") or payload.get("attachments") or []
    attachment_jobs: list[dict] = []
    for i, att in enumerate(raw_attachments):
        if not isinstance(att, dict):
            continue
        url = att.get("data_url") or att.get("url") or att.get("thumb_url")
        if not url:
            continue
        file_key = att.get("file_key") or att.get("id", f"att_{i}")
        ref = hashlib.sha256(str(file_key).encode()).hexdigest()[:24]
        attachment_jobs.append(
            {
                "ref": ref,
                "url": url,
                "content_type": att.get("content_type") or "application/octet-stream",
                "type": att.get("file_type", "file"),
            }
        )

    sender_raw = message_raw.get("sender") or payload.get("sender") or {}
    sender_type = str(sender_raw.get("type", "contact"))
    sender_ref = str(sender_raw.get("id", ""))

    rabbitmq_conn = getattr(request.app.state, "rabbitmq", None)
    background_tasks.add_task(
        _queue_processing_job,
        tenant_id=str(tenant_ctx.tenant_id),
        conversation_id=normalized.conversation_id,
        correlation_id=correlation_id,
        idempotency_key=normalized.idempotency_key,
        raw_hash=normalized.raw_event_ref.raw_hash,
        content=normalized.content_text,
        attachments=attachment_jobs,
        sender_type=sender_type,
        sender_ref=sender_ref,
        rabbitmq_connection=rabbitmq_conn,
    )

    logger.info(
        "webhook_event_accepted",
        correlation_id=correlation_id,
        conversation_id=normalized.conversation_id,
        event_type=normalized.event_type,
    )

    webhook_requests_total.labels(status="accepted").inc()
    webhook_request_duration_seconds.observe(time.monotonic() - _t_start)
    return {
        "status": "accepted",
        "correlation_id": correlation_id,
    }


async def _queue_processing_job(
    tenant_id: str,
    conversation_id: str,
    correlation_id: str,
    idempotency_key: str,
    raw_hash: str,
    content: str | None = None,
    attachments: list[dict] | None = None,
    sender_type: str = "contact",
    sender_ref: str = "",
    rabbitmq_connection: object | None = None,
) -> None:
    """Queue a conversation processing job to RabbitMQ.

    Args:
        tenant_id: Tenant UUID string.
        conversation_id: Conversation identifier.
        correlation_id: Tracing ID.
        idempotency_key: Deduplication key.
        raw_hash: Hash reference of the raw event.
        content: Message text (optional).
        attachments: Attachment job descriptors (optional).
        sender_type: Sender type string.
        sender_ref: Sender identifier.
        rabbitmq_connection: Live aio_pika connection from app.state, or None.
    """
    log = logger.bind(
        correlation_id=correlation_id,
        conversation_id=conversation_id,
        tenant_ref=tenant_id[:8],
    )

    if rabbitmq_connection is None:
        log.warning("rabbitmq_unavailable_job_not_published")
        return

    try:
        from iara.messaging.publisher import ConversationJob, MessagePublisher
        from iara.messaging.topology import declare_topology

        channel = await rabbitmq_connection.channel()  # type: ignore[attr-defined]
        await declare_topology(channel)
        publisher = MessagePublisher(channel=channel)
        job = ConversationJob(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            event_ref=raw_hash,
            content=content,
            attachments=attachments or [],
            sender_type=sender_type,
            sender_ref=sender_ref,
        )
        await publisher.publish_conversation_job(job)
        await channel.close()
        log.info("job_published")
    except Exception as exc:
        log.error(
            "job_publish_failed",
            error_code=type(exc).__name__,
            error_summary=str(exc)[:200],
        )
