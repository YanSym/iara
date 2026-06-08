"""Synthetic event payloads for testing.

All data is synthetic — no real account IDs, phone numbers, or personal data.
These fixtures are used across unit, integration, and security tests.
"""

from __future__ import annotations

import json
import uuid
from typing import Any


def make_incoming_message_payload(
    account_id: str = "11111",
    inbox_id: str = "inbox_001",
    conversation_id: str = "conv_001",
    message_id: str = "msg_001",
    content: str = "Hello, I'd like to schedule an appointment.",
    sender_type: str = "contact",
    is_private: bool = False,
) -> dict[str, Any]:
    """Create a synthetic incoming message payload.

    Args:
        account_id: Synthetic account ID (not a real one).
        inbox_id: Synthetic inbox ID.
        conversation_id: Synthetic conversation ID.
        message_id: Synthetic message ID.
        content: Message content.
        sender_type: Sender type (contact, agent_bot, user, system).
        is_private: Whether this is a private note.

    Returns:
        dict[str, Any]: Synthetic Chatwoot message payload.
    """
    return {
        "event": "message_created",
        "id": str(uuid.uuid4()),
        "account": {"id": account_id, "name": "Synthetic Corp"},
        "inbox": {
            "id": inbox_id,
            "channel_type": "Channel::Whatsapp",
            "name": "Synthetic WhatsApp",
        },
        "conversation": {
            "id": conversation_id,
            "status": "open",
            "meta": {
                "sender": {
                    "type": sender_type,
                    "name": "Synthetic User",
                    # Deliberately NO phone number in fixtures
                }
            },
        },
        "message": {
            "id": message_id,
            "content": content,
            "message_type": "incoming",
            "private": is_private,
            "sender": {"type": sender_type},
            "attachments": [],
        },
    }


def make_outgoing_message_payload(
    account_id: str = "11111",
    conversation_id: str = "conv_001",
) -> dict[str, Any]:
    """Create a synthetic outgoing message payload.

    Args:
        account_id: Synthetic account ID.
        conversation_id: Synthetic conversation ID.

    Returns:
        dict[str, Any]: Synthetic outgoing message payload.
    """
    return {
        "event": "message_created",
        "id": str(uuid.uuid4()),
        "account": {"id": account_id},
        "inbox": {"id": "inbox_001", "channel_type": "Channel::Whatsapp"},
        "conversation": {"id": conversation_id, "status": "open"},
        "message": {
            "id": str(uuid.uuid4()),
            "content": "I can help you with that.",
            "message_type": "outgoing",
            "private": False,
            "sender": {"type": "agent_bot"},
        },
    }


def make_bot_message_payload(
    account_id: str = "11111",
    conversation_id: str = "conv_001",
) -> dict[str, Any]:
    """Create a synthetic bot-sent message payload.

    Args:
        account_id: Synthetic account ID.
        conversation_id: Synthetic conversation ID.

    Returns:
        dict[str, Any]: Synthetic bot message payload.
    """
    return {
        "event": "message_created",
        "id": str(uuid.uuid4()),
        "account": {"id": account_id},
        "inbox": {"id": "inbox_001", "channel_type": "Channel::Whatsapp"},
        "conversation": {"id": conversation_id},
        "message": {
            "id": str(uuid.uuid4()),
            "content": "Automated response.",
            "message_type": "incoming",
            "private": False,
            "sender": {"type": "agent_bot"},
        },
    }


def make_private_note_payload(
    account_id: str = "11111",
    conversation_id: str = "conv_001",
) -> dict[str, Any]:
    """Create a synthetic private note payload.

    Private notes must never enter agent context.

    Args:
        account_id: Synthetic account ID.
        conversation_id: Synthetic conversation ID.

    Returns:
        dict[str, Any]: Synthetic private note payload.
    """
    return {
        "event": "message_created",
        "id": str(uuid.uuid4()),
        "account": {"id": account_id},
        "inbox": {"id": "inbox_001", "channel_type": "Channel::Whatsapp"},
        "conversation": {"id": conversation_id},
        "message": {
            "id": str(uuid.uuid4()),
            "content": "Private note: This lead is interesting.",
            "message_type": "incoming",
            "private": True,
            "sender": {"type": "user"},
        },
    }


def make_audio_attachment_payload(
    account_id: str = "11111",
    conversation_id: str = "conv_001",
) -> dict[str, Any]:
    """Create a synthetic audio attachment payload.

    The base64 field is intentionally not included (per security policy).

    Args:
        account_id: Synthetic account ID.
        conversation_id: Synthetic conversation ID.

    Returns:
        dict[str, Any]: Synthetic audio attachment payload.
    """
    return {
        "event": "message_created",
        "id": str(uuid.uuid4()),
        "account": {"id": account_id},
        "inbox": {"id": "inbox_001", "channel_type": "Channel::Whatsapp"},
        "conversation": {"id": conversation_id},
        "message": {
            "id": str(uuid.uuid4()),
            "content": "",
            "message_type": "incoming",
            "private": False,
            "sender": {"type": "contact"},
            "attachments": [
                {
                    "id": "att_001",
                    "file_key": "synthetic_audio_key",
                    "file_type": "audio",
                    "content_type": "audio/ogg",
                    "file_size": 45000,
                    "filename": "voice_message.ogg",
                    # No raw URL, no base64
                }
            ],
        },
    }


def make_wrong_account_payload(
    wrong_account_id: str = "99999",
    conversation_id: str = "conv_001",
) -> dict[str, Any]:
    """Create a payload with a mismatched account ID for cross-tenant testing.

    Args:
        wrong_account_id: An account ID that does not match the tenant binding.
        conversation_id: Synthetic conversation ID.

    Returns:
        dict[str, Any]: Synthetic payload with wrong account ID.
    """
    return {
        "event": "message_created",
        "id": str(uuid.uuid4()),
        "account": {"id": wrong_account_id},
        "inbox": {"id": "inbox_001", "channel_type": "Channel::Whatsapp"},
        "conversation": {"id": conversation_id},
        "message": {
            "id": str(uuid.uuid4()),
            "content": "This should be rejected.",
            "message_type": "incoming",
            "private": False,
            "sender": {"type": "contact"},
        },
    }


def make_payload_with_sensitive_fields() -> dict[str, Any]:
    """Create a payload that contains sensitive fields for redaction testing.

    These fields must NEVER appear in normalized event output or logs.

    Returns:
        dict[str, Any]: Synthetic payload with fields that trigger redaction.
    """
    return {
        "event": "message_created",
        "id": str(uuid.uuid4()),
        "account": {"id": "11111"},
        "inbox": {"id": "inbox_001", "channel_type": "Channel::Whatsapp"},
        "conversation": {"id": "conv_001"},
        "message": {
            "id": str(uuid.uuid4()),
            "content": "Normal message content",
            "message_type": "incoming",
            "private": False,
            "sender": {
                "type": "contact",
                # These sensitive fields should be stripped by redaction
                "phone": "+5511999999999",
                "email": "test@example.com",
            },
        },
        # Top-level sensitive fields that should never be persisted
        "api_token": "sk-ant-fake-token-for-testing",
        "headers": {"Authorization": "Bearer fake_token"},
    }


def make_synthetic_tenant_record(
    tenant_key: str = "test_tenant_001",
    provider_account_id: str = "11111",
) -> dict[str, Any]:
    """Create a synthetic tenant database record for testing.

    Args:
        tenant_key: The public webhook key.
        provider_account_id: The provider account ID (synthetic).

    Returns:
        dict[str, Any]: Synthetic tenant record.
    """
    return {
        "tenant_id": str(uuid.uuid4()),
        "name": "Synthetic Test Tenant",
        "status": "active",
        "provider": "chatwoot",
        "provider_account_id": provider_account_id,
    }


def payload_to_bytes(payload: dict[str, Any]) -> bytes:
    """Convert a payload dict to bytes for hash computation.

    Args:
        payload: The payload to convert.

    Returns:
        bytes: JSON-encoded payload bytes.
    """
    return json.dumps(payload, sort_keys=True).encode()
