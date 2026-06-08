"""Scheduling tool handlers — availability, schedule, cancel, reschedule.

All write operations emit ProviderCommands to the outbox; they never execute
provider calls directly (INV-04). Read operations return sanitized data.
"""

from __future__ import annotations

import uuid
from typing import Any

from iara.observability.logging import get_logger

logger = get_logger(__name__)


async def handle_availability(arguments: dict[str, Any]) -> dict[str, Any]:
    """Check available appointment slots.

    Args:
        arguments: Validated tool arguments (date_range_start, date_range_end, service_type).

    Returns:
        dict[str, Any]: Sanitized availability summary (counts, next slot — no PII).
    """
    date_start = arguments.get("date_range_start", "")
    date_end = arguments.get("date_range_end", "")
    service_type = arguments.get("service_type", "general")

    logger.info(
        "tool_availability_check",
        date_start=date_start,
        date_end=date_end,
        service_type=service_type,
    )

    # In production: delegate to Google Calendar / ClinicOrp MCP via outbox.
    # Return counts and the next available slot — never raw contact data.
    return {
        "available_slots_count": 3,
        "next_available_slot": f"{date_start}T09:00:00",
        "service_type": service_type,
        "note": "Availability is illustrative; connect Google Calendar MCP for real data.",
    }


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
