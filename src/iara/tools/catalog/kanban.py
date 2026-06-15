"""Kanban tool handlers — analyze, update status, comment.

All writes are gated by KanbanMode policy (suggest_only by default per INV-06).
Read-only analysis returns a sanitized suggestion — no mutations.
"""

from __future__ import annotations

import uuid
from typing import Any

from iara.config_publishing.registry import get_kanban_stages
from iara.observability.logging import get_logger

logger = get_logger(__name__)

# Fallback for code paths that don't have a tenant_id (e.g. static validation)
_DEFAULT_KANBAN_STAGES = [
    "new_lead",
    "contacted",
    "nurturing",
    "qualified",
    "proposal_sent",
    "negotiation",
    "won",
    "lost",
]


async def handle_kanban_analyze(arguments: dict[str, Any]) -> dict[str, Any]:
    """Analyze conversation to suggest a kanban stage.

    Read-only — returns a suggestion only. No mutations.

    Args:
        arguments: Tool arguments (include_history).

    Returns:
        dict[str, Any]: Suggested stage with confidence and rationale.
    """
    include_history = arguments.get("include_history", False)

    logger.info(
        "tool_kanban_analyze",
        include_history=include_history,
    )

    # In production: call a classification model or rules engine.
    return {
        "suggested_stage": "nurturing",
        "confidence": 0.72,
        "rationale": "Conversation indicates interest but no clear buying signal yet.",
        "mode": "suggest_only",
        "note": "This is a suggestion — human review required before update.",
    }


def build_kanban_update_command(
    arguments: dict[str, Any],
    tenant_id: str,
    conversation_id: str,
    idempotency_key: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Build a ProviderCommand to update the conversation's kanban stage.

    Args:
        arguments: Tool arguments (stage, reason).
        tenant_id: Tenant UUID.
        conversation_id: Conversation.
        idempotency_key: Deduplication key.
        correlation_id: Tracing ID.

    Returns:
        dict[str, Any]: Outbox command payload.
    """
    stage = arguments.get("stage", "")
    reason = arguments.get("reason", "")

    valid_stages = get_kanban_stages(tenant_id)
    if stage not in valid_stages:
        logger.warning(
            "kanban_unknown_stage",
            stage=stage,
            valid_stages=valid_stages,
        )

    logger.info(
        "tool_kanban_update",
        stage=stage,
        has_reason=bool(reason),
        conversation_id=conversation_id,
    )

    return {
        "command_id": str(uuid.uuid4()),
        "provider": "chatwoot",
        "capability_name": "update_conversation_stage",
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "parameters": {
            "stage": stage,
            "label": f"kanban:{stage}",
        },
    }


def build_kanban_comment_command(
    arguments: dict[str, Any],
    tenant_id: str,
    conversation_id: str,
    idempotency_key: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Build a ProviderCommand to add a private kanban tracking note.

    Args:
        arguments: Tool arguments (comment).
        tenant_id: Tenant UUID.
        conversation_id: Conversation.
        idempotency_key: Deduplication key.
        correlation_id: Tracing ID.

    Returns:
        dict[str, Any]: Outbox command payload.
    """
    import hashlib

    comment = arguments.get("comment", "")
    comment_ref = "comment:" + hashlib.sha256(comment.encode()).hexdigest()[:16]

    logger.info(
        "tool_kanban_comment",
        comment_ref=comment_ref,
        conversation_id=conversation_id,
    )

    return {
        "command_id": str(uuid.uuid4()),
        "provider": "chatwoot",
        "capability_name": "send_private_note",
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "parameters": {
            "note_ref": comment_ref,
            "content_length": len(comment),
        },
    }
