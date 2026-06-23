"""Follow-up tool handlers — re-engagement and scheduled follow-ups.

``followup_reengage_conversation`` sends a message to resume a stalled
conversation immediately (via outbox). ``followup_schedule`` enqueues a
future message in follow_up_queue; the FollowUpSchedulerWorker delivers it
at trigger_at. Both respect quiet hours and opt-out signals.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from iara.observability.logging import get_logger

logger = get_logger(__name__)

# Default quiet-hours window (local time 22:00 – 08:00 UTC). Tenants may
# override via settings; this is a safe global default.
_QUIET_HOURS_START = 22  # 22:00 UTC
_QUIET_HOURS_END = 8  # 08:00 UTC


def _is_quiet_hour(dt: datetime) -> bool:
    """Return True if *dt* falls within the global quiet-hours window."""
    hour = dt.hour
    if _QUIET_HOURS_START > _QUIET_HOURS_END:
        return hour >= _QUIET_HOURS_START or hour < _QUIET_HOURS_END
    return _QUIET_HOURS_START <= hour < _QUIET_HOURS_END


def _next_non_quiet_time(trigger_at: datetime) -> datetime:
    """Advance *trigger_at* past the quiet-hours window if needed."""
    if not _is_quiet_hour(trigger_at):
        return trigger_at
    # Roll forward to 08:00 UTC same or next day.
    candidate = trigger_at.replace(hour=_QUIET_HOURS_END, minute=0, second=0, microsecond=0)
    if candidate <= trigger_at:
        candidate = candidate + timedelta(days=1)
    return candidate


def build_followup_command(
    arguments: dict[str, Any],
    tenant_id: str,
    conversation_id: str,
    idempotency_key: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Build a ProviderCommand to send a follow-up message.

    Args:
        arguments: Tool arguments (message, reason).
        tenant_id: Tenant UUID.
        conversation_id: Conversation to re-engage.
        idempotency_key: Deduplication key.
        correlation_id: Tracing ID.

    Returns:
        dict[str, Any]: Outbox command payload.
    """
    message = arguments.get("message", "")
    reason = arguments.get("reason", "")

    # Store hash ref of message content — never raw PII (INV-05)
    message_ref = "msg:" + hashlib.sha256(message.encode()).hexdigest()[:16]

    logger.info(
        "tool_followup_reengage",
        message_ref=message_ref,
        has_reason=bool(reason),
        conversation_id=conversation_id,
    )

    return {
        "command_id": str(uuid.uuid4()),
        "provider": "chatwoot",
        "capability_name": "followup_reengage_conversation",
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "parameters": {
            "conversation_id": conversation_id,
            "message_ref": message_ref,
            "message_length": len(message),
            "reason_ref": hashlib.sha256(reason.encode()).hexdigest()[:16] if reason else "",
        },
    }


def build_followup_schedule_payload(
    arguments: dict[str, Any],
    tenant_id: str,
    conversation_id: str,
    idempotency_key: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Build the follow_up_queue payload for a scheduled follow-up message.

    Applies quiet-hours adjustment and opt-out checks. Returns a payload
    with ``status="skipped"`` when the send should be suppressed.

    Args:
        arguments: Tool arguments (message, delay_hours, trigger_at_iso, reason,
                   contact_ref, opted_out).
        tenant_id: Tenant UUID string.
        conversation_id: Conversation to follow up on.
        idempotency_key: Deduplication key.
        correlation_id: Tracing ID.

    Returns:
        dict[str, Any]: Payload for ``FollowUpRepository.enqueue_raw()``, or
        a dict with ``status="skipped"`` when the send must be suppressed.
    """
    message = arguments.get("message", "")
    reason = arguments.get("reason", "")
    opted_out = bool(arguments.get("opted_out", False))

    # Build hash refs — never store raw text (INV-05)
    message_ref = "msg:" + hashlib.sha256(message.encode()).hexdigest()[:16]
    reason_ref = hashlib.sha256(reason.encode()).hexdigest()[:16] if reason else ""
    contact_ref = str(arguments.get("contact_ref", ""))

    # Determine trigger_at
    now = datetime.now(UTC)
    trigger_at_iso = arguments.get("trigger_at_iso", "")
    if trigger_at_iso:
        try:
            trigger_at = datetime.fromisoformat(trigger_at_iso)
            if trigger_at.tzinfo is None:
                trigger_at = trigger_at.replace(tzinfo=UTC)
        except ValueError:
            trigger_at = now + timedelta(hours=1)
    else:
        delay_hours = float(arguments.get("delay_hours", 1.0))
        trigger_at = now + timedelta(hours=max(0.0, delay_hours))

    # Enforce quiet hours — delay into the next active window if needed
    trigger_at = _next_non_quiet_time(trigger_at)

    # Opt-out: do not schedule
    if opted_out:
        logger.info(
            "follow_up_schedule_skipped_opted_out",
            conversation_id=conversation_id,
        )
        return {
            "status": "skipped",
            "skip_reason": "opted_out",
            "idempotency_key": idempotency_key,
        }

    logger.info(
        "follow_up_schedule_payload_built",
        trigger_at=trigger_at.isoformat(),
        message_ref=message_ref,
        conversation_id=conversation_id,
    )

    return {
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "contact_ref": contact_ref,
        "message_ref": message_ref,
        "message_length": len(message),
        "reason_ref": reason_ref,
        "trigger_at": trigger_at.isoformat(),
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "max_attempts": int(arguments.get("max_attempts", 3)),
        "opted_out": False,
        "status": "pending",
    }
