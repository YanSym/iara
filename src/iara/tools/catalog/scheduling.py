"""Scheduling tool handlers — availability, schedule, cancel, reschedule.

All write operations emit ProviderCommands to the outbox; they never execute
provider calls directly (INV-04). Read operations return sanitized data.

The module-level ``_SCHEDULING_ADAPTER`` singleton is set by
``build_production_graph`` at startup. Defaults to NullSchedulingAdapter so
availability checks degrade gracefully when no backend is configured.
"""

from __future__ import annotations

import uuid
from typing import Any

from iara.observability.logging import get_logger
from iara.provider.scheduling.null_adapter import NullSchedulingAdapter

logger = get_logger(__name__)

# Injected by build_production_graph → _inject_scheduling_adapter().
_SCHEDULING_ADAPTER: Any = NullSchedulingAdapter()


async def handle_availability(arguments: dict[str, Any]) -> dict[str, Any]:
    """Check available appointment slots via the configured scheduling backend.

    Delegates to the module-level ``_SCHEDULING_ADAPTER``. Never returns PII.

    Args:
        arguments: Validated tool arguments (date_range_start, date_range_end,
            service_type, tenant_id, calendar_id).

    Returns:
        dict[str, Any]: Sanitized availability summary (counts, next slot — no PII).
    """
    date_start = arguments.get("date_range_start", "")
    date_end = arguments.get("date_range_end", "")
    service_type = arguments.get("service_type", "general")
    tenant_id = arguments.get("tenant_id", "")
    calendar_id = arguments.get("calendar_id", "primary")

    logger.info(
        "tool_availability_check",
        date_start=date_start,
        date_end=date_end,
        service_type=service_type,
        provider=getattr(_SCHEDULING_ADAPTER, "provider_name", "unknown"),
    )

    return await _SCHEDULING_ADAPTER.check_availability(
        tenant_id=tenant_id,
        date_range_start=date_start,
        date_range_end=date_end,
        service_type=service_type,
        calendar_id=calendar_id,
    )


def build_schedule_command(
    arguments: dict[str, Any],
    tenant_id: str,
    conversation_id: str,
    idempotency_key: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Build a ProviderCommand payload for scheduling an appointment.

    Args:
        arguments: Tool arguments (datetime_iso, service_type, notes).
        tenant_id: Tenant UUID.
        conversation_id: Conversation this schedule belongs to.
        idempotency_key: Deduplication key.
        correlation_id: Tracing ID.

    Returns:
        dict[str, Any]: Outbox command payload.
    """
    return {
        "command_id": str(uuid.uuid4()),
        "provider": "google_calendar",
        "capability_name": "schedule_appointment",
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "parameters": {
            "datetime_iso": arguments.get("datetime_iso"),
            "service_type": arguments.get("service_type", "general"),
            "notes_ref": _hash_notes(arguments.get("notes", "")),
        },
    }


def build_cancel_command(
    arguments: dict[str, Any],
    tenant_id: str,
    conversation_id: str,
    idempotency_key: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Build a ProviderCommand payload for cancelling an appointment.

    Args:
        arguments: Tool arguments (appointment_ref, reason).
        tenant_id: Tenant UUID.
        conversation_id: Conversation this cancel belongs to.
        idempotency_key: Deduplication key.
        correlation_id: Tracing ID.

    Returns:
        dict[str, Any]: Outbox command payload.
    """
    return {
        "command_id": str(uuid.uuid4()),
        "provider": "google_calendar",
        "capability_name": "cancel_appointment",
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "parameters": {
            "appointment_ref": arguments.get("appointment_ref"),
            "reason_ref": _hash_notes(arguments.get("reason", "")),
        },
    }


def build_reschedule_command(
    arguments: dict[str, Any],
    tenant_id: str,
    conversation_id: str,
    idempotency_key: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Build a ProviderCommand payload for rescheduling an appointment.

    Args:
        arguments: Tool arguments (appointment_ref, new_datetime_iso).
        tenant_id: Tenant UUID.
        conversation_id: Conversation.
        idempotency_key: Deduplication key.
        correlation_id: Tracing ID.

    Returns:
        dict[str, Any]: Outbox command payload.
    """
    return {
        "command_id": str(uuid.uuid4()),
        "provider": "google_calendar",
        "capability_name": "reschedule_appointment",
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "parameters": {
            "appointment_ref": arguments.get("appointment_ref"),
            "new_datetime_iso": arguments.get("new_datetime_iso"),
        },
    }


def _hash_notes(notes: str) -> str:
    """Replace sensitive notes with an opaque reference hash.

    Args:
        notes: Raw note text.

    Returns:
        str: Opaque SHA-256-based reference.
    """
    import hashlib

    if not notes:
        return ""
    return "notes:" + hashlib.sha256(notes.encode()).hexdigest()[:16]
