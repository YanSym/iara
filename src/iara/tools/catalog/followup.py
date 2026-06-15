"""Follow-up tool handler — followup_reengage_conversation.

Re-engagement sends a message to resume a stalled conversation.
Gated by draft_only policy — produces a draft pending human approval by default.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from iara.observability.logging import get_logger

logger = get_logger(__name__)


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
