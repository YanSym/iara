"""Lead qualification tool handlers — qualify, disqualify.

Emits ProviderCommands to the outbox (INV-04). Never directly mutates provider state.
"""

from __future__ import annotations

import uuid
from typing import Any

from iara.observability.logging import get_logger

logger = get_logger(__name__)


def build_qualify_command(
    arguments: dict[str, Any],
    tenant_id: str,
    conversation_id: str,
    idempotency_key: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Build a ProviderCommand payload to qualify a lead.

    Adds a private note and label to the Chatwoot conversation.

    Args:
        arguments: Tool arguments (qualification_note, label).
        tenant_id: Tenant UUID.
        conversation_id: Conversation to qualify.
        idempotency_key: Deduplication key.
        correlation_id: Tracing ID.

    Returns:
        dict[str, Any]: Outbox command payload.
    """
    label = arguments.get("label", "qualified")
    note = arguments.get("qualification_note", "")

    logger.info(
        "tool_qualify_lead",
        label=label,
        has_note=bool(note),
        conversation_id=conversation_id,
    )

    return {
        "command_id": str(uuid.uuid4()),
        "provider": "chatwoot",
        "capability_name": "label_conversation",
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "parameters": {
            "label": label,
            "private_note_hash": _hash_content(note),
        },
    }


def build_disqualify_command(
    arguments: dict[str, Any],
    tenant_id: str,
    conversation_id: str,
    idempotency_key: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Build a ProviderCommand payload to disqualify a lead.

    Adds a disqualified label and records the reason as a private note.

    Args:
        arguments: Tool arguments (reason, label).
        tenant_id: Tenant UUID.
        conversation_id: Conversation to disqualify.
        idempotency_key: Deduplication key.
        correlation_id: Tracing ID.

    Returns:
        dict[str, Any]: Outbox command payload.
    """
    label = arguments.get("label", "disqualified")
    reason = arguments.get("reason", "")

    logger.info(
        "tool_disqualify_lead",
        label=label,
        has_reason=bool(reason),
        conversation_id=conversation_id,
    )

    return {
        "command_id": str(uuid.uuid4()),
        "provider": "chatwoot",
        "capability_name": "label_conversation",
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "parameters": {
            "label": label,
            "reason_ref": _hash_content(reason),
        },
    }


def _hash_content(content: str) -> str:
    """Create an opaque content reference from raw text.

    Args:
        content: Raw text content.

    Returns:
        str: Opaque SHA-256-based reference, or empty string.
    """
    import hashlib

    if not content:
        return ""
    return "ref:" + hashlib.sha256(content.encode()).hexdigest()[:16]
