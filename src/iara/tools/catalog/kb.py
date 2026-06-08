"""Knowledge Base tool handler — kb_suggest_update.

KB suggestions are always draft-only: the agent proposes an update, a human
reviews and publishes. The raw content is never written directly to the KB.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from iara.observability.logging import get_logger

logger = get_logger(__name__)


def build_kb_suggest_command(
    arguments: dict[str, Any],
    tenant_id: str,
    conversation_id: str,
    idempotency_key: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Build a draft KB suggestion record.

    The suggestion is stored as a draft in the config publishing pipeline.
    A human reviewer must approve it before it affects the live KB.

    Args:
        arguments: Tool arguments (topic, suggested_content, rationale).
        tenant_id: Tenant UUID.
        conversation_id: Conversation that triggered this suggestion.
        idempotency_key: Deduplication key.
        correlation_id: Tracing ID.

    Returns:
        dict[str, Any]: Outbox command payload for the KB draft pipeline.
    """
    topic = arguments.get("topic", "")
    suggested_content = arguments.get("suggested_content", "")
    rationale = arguments.get("rationale", "")

    # Store hash refs — never raw knowledge base content in outbox (INV-05)
    topic_ref = "topic:" + hashlib.sha256(topic.encode()).hexdigest()[:16]
    content_ref = "kb_content:" + hashlib.sha256(suggested_content.encode()).hexdigest()[:16]
    draft_id = str(uuid.uuid4())[:8]

    logger.info(
        "tool_kb_suggest_update",
        topic_ref=topic_ref,
        content_ref=content_ref,
        draft_id=draft_id,
        conversation_id=conversation_id,
    )

    return {
        "command_id": str(uuid.uuid4()),
        "provider": "internal_kb",
        "capability_name": "create_kb_draft",
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "parameters": {
            "topic_ref": topic_ref,
            "content_ref": content_ref,
            "rationale_ref": (
                hashlib.sha256(rationale.encode()).hexdigest()[:16] if rationale else ""
            ),
            "draft_id": draft_id,
            "content_length": len(suggested_content),
        },
    }


async def handle_kb_suggest(arguments: dict[str, Any]) -> dict[str, Any]:
    """Handle a KB suggestion in read/draft mode (no real mutations).

    Args:
        arguments: Tool arguments (topic, suggested_content, rationale).

    Returns:
        dict[str, Any]: Draft reference summary.
    """
    topic = arguments.get("topic", "")
    suggested_content = arguments.get("suggested_content", "")

    draft_ref = "kb_draft:" + str(uuid.uuid4())[:8]

    logger.info(
        "tool_kb_suggest_draft",
        topic_preview=topic[:30],
        content_length=len(suggested_content),
        draft_ref=draft_ref,
    )

    return {
        "draft_ref": draft_ref,
        "topic_preview": topic[:50],
        "content_length": len(suggested_content),
        "status": "draft",
        "note": "KB suggestion created as draft. Human review required before publishing.",
    }
