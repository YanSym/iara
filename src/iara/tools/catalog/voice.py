"""Voice tool handler — voice_respond_audio.

Audio responses require an explicit voice_output_policy. If the policy is not
active, the tool falls back to returning the text content for regular dispatch.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from iara.observability.logging import get_logger

logger = get_logger(__name__)


def build_voice_command(
    arguments: dict[str, Any],
    tenant_id: str,
    conversation_id: str,
    idempotency_key: str,
    correlation_id: str,
    voice_output_enabled: bool = False,
) -> dict[str, Any]:
    """Build a ProviderCommand to respond with audio.

    If voice output is not enabled for this tenant, falls back to text.

    Args:
        arguments: Tool arguments (text_content, voice_ref).
        tenant_id: Tenant UUID.
        conversation_id: Conversation.
        idempotency_key: Deduplication key.
        correlation_id: Tracing ID.
        voice_output_enabled: Whether voice output is active for this tenant.

    Returns:
        dict[str, Any]: Outbox command payload.
    """
    text_content = arguments.get("text_content", "")
    voice_ref = arguments.get("voice_ref", "default")

    # Store hash ref of content — never raw text in outbox (INV-05)
    content_ref = "audio_content:" + hashlib.sha256(text_content.encode()).hexdigest()[:16]

    if not voice_output_enabled:
        logger.info(
            "voice_output_fallback_to_text",
            content_ref=content_ref,
            conversation_id=conversation_id,
        )
        return {
            "command_id": str(uuid.uuid4()),
            "provider": "chatwoot",
            "capability_name": "send_outbound_message",
            "tenant_id": tenant_id,
            "conversation_id": conversation_id,
            "idempotency_key": idempotency_key,
            "correlation_id": correlation_id,
            "parameters": {
                "content_ref": content_ref,
                "content_length": len(text_content),
                "fallback_reason": "voice_output_policy_not_active",
            },
        }

    logger.info(
        "tool_voice_respond_audio",
        content_ref=content_ref,
        voice_ref=voice_ref,
        conversation_id=conversation_id,
    )

    return {
        "command_id": str(uuid.uuid4()),
        "provider": "voice_tts",
        "capability_name": "generate_and_send_audio",
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "parameters": {
            "content_ref": content_ref,
            "content_length": len(text_content),
            "voice_ref": voice_ref,
        },
    }
