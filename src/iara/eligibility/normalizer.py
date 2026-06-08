"""Chatwoot webhook event normalizer.

Transforms a raw Chatwoot webhook payload into a ``NormalizedChatwootEvent``.
The raw payload is NEVER stored — only a ``RawEventRef`` (hash) is kept.

Security invariants enforced here:
- Private notes are flagged but their content is NOT stored.
- Raw phone numbers are NOT extracted into normalized fields.
- Account IDs are stored only as opaque refs.
- Temporary URLs and base64 data from attachments are stripped.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from iara.contracts.events import (
    AttachmentType,
    CanonicalAttachment,
    EventType,
    MessageType,
    NormalizedChatwootEvent,
    RawEventRef,
    SenderType,
)
from iara.contracts.tenancy import TenantContext
from iara.observability.logging import get_logger

logger = get_logger(__name__)


def _make_account_ref(account_id: Any) -> str:
    """Create an opaque account reference from a real account ID.

    The real account ID is hashed and truncated for use in logs and refs.

    Args:
        account_id: The real Chatwoot account ID.

    Returns:
        str: Opaque account reference.
    """
    digest = hashlib.sha256(str(account_id).encode()).hexdigest()
    return f"acct:{digest[:12]}"


def _make_idempotency_key(event_id: str, tenant_key: str) -> str:
    """Derive an idempotency key for the event.

    Args:
        event_id: The Chatwoot event UUID.
        tenant_key: The tenant key.

    Returns:
        str: A deterministic idempotency key.
    """
    combined = f"{tenant_key}:{event_id}"
    return hashlib.sha256(combined.encode()).hexdigest()


def _normalize_attachment(raw: dict[str, Any]) -> CanonicalAttachment:
    """Normalize a raw attachment dict into a CanonicalAttachment.

    Strips temporary URLs, base64 data, and raw file bytes.

    Args:
        raw: Raw attachment dict from Chatwoot.

    Returns:
        CanonicalAttachment: Normalized attachment with opaque ref.
    """
    # Create opaque ref from file_key or a hash of available metadata
    file_key = raw.get("file_key") or raw.get("id", "unknown")
    attachment_ref = hashlib.sha256(str(file_key).encode()).hexdigest()[:24]

    attachment_type_raw = raw.get("file_type", "file")
    try:
        attachment_type = AttachmentType(attachment_type_raw)
    except ValueError:
        attachment_type = AttachmentType.FILE

    return CanonicalAttachment(
        attachment_type=attachment_type,
        content_type=raw.get("content_type"),
        file_name=raw.get("filename"),
        file_size_bytes=raw.get("file_size"),
        attachment_ref=attachment_ref,
        is_processed=False,
        # _raw_url and _raw_base64 are intentionally NOT set here
    )


class ChatwootEventNormalizer:
    """Normalizes raw Chatwoot webhook payloads into canonical event contracts.

    This class implements the boundary between the raw provider world and the
    runtime. It enforces all data sanitization rules:
    - Never stores raw payload (only hash)
    - Never stores real account IDs (only opaque refs)
    - Never stores private note content
    - Never stores temporary URLs or base64 data
    - Strips unknown/extra fields

    Args:
        tenant_context: The verified tenant context for account binding.
    """

    def __init__(self, tenant_context: TenantContext) -> None:
        self._tenant = tenant_context

    def normalize(
        self,
        raw_payload: dict[str, Any],
        raw_bytes: bytes,
        correlation_id: str | None = None,
    ) -> NormalizedChatwootEvent:
        """Normalize a raw Chatwoot webhook payload.

        Args:
            raw_payload: The parsed JSON payload (already deserialized).
            raw_bytes: The original raw bytes (for hashing only — not stored).
            correlation_id: Optional pre-assigned correlation ID.

        Returns:
            NormalizedChatwootEvent: Canonical normalized event.

        Raises:
            ValueError: If required fields are missing.
        """
        now_iso = datetime.now(UTC).isoformat()
        corr_id = correlation_id or str(uuid.uuid4())
        raw_ref = RawEventRef.from_raw_bytes(raw_bytes, received_at=now_iso)

        # Extract event metadata
        event_type_raw = raw_payload.get("event") or raw_payload.get("event_type", "")
        try:
            event_type = EventType(event_type_raw)
        except ValueError:
            event_type = EventType.MESSAGE_CREATED  # Safe fallback for unknown event types

        # Event ID
        event_id = str(raw_payload.get("id") or str(uuid.uuid4()))

        # Account ID — store only opaque ref
        account_raw = raw_payload.get("account", {}) or {}
        real_account_id = str(account_raw.get("id", raw_payload.get("account_id", "")))
        account_id_ref = _make_account_ref(real_account_id)

        # Inbox
        inbox_raw = raw_payload.get("inbox", {}) or {}
        inbox_id = str(inbox_raw.get("id", raw_payload.get("inbox_id", "unknown")))
        inbox_channel_type = str(
            inbox_raw.get("channel_type", raw_payload.get("channel_type", "unknown"))
        )

        # Conversation
        conversation_raw = raw_payload.get("conversation", {}) or {}
        conversation_id = str(
            conversation_raw.get("id", raw_payload.get("conversation_id", "unknown"))
        )

        # Message-specific fields
        message_raw = raw_payload.get("message", {}) or raw_payload
        message_id = str(message_raw.get("id", "")) or None

        message_type_raw = message_raw.get("message_type", raw_payload.get("message_type"))
        try:
            message_type = (
                MessageType(str(message_type_raw)) if message_type_raw is not None else None
            )
        except ValueError:
            message_type = None

        sender_raw = message_raw.get("sender") or raw_payload.get("sender") or {}
        sender_type_raw = sender_raw.get("type") or message_raw.get("sender_type")
        try:
            sender_type = SenderType(str(sender_type_raw)) if sender_type_raw else None
        except ValueError:
            sender_type = None

        # Content — private notes are flagged but content is stripped
        is_private = bool(message_raw.get("private", False))
        if is_private:
            content_text = None  # Never store private note content
            logger.debug("private_note_detected_stripped", correlation_id=corr_id)
        else:
            content_text = message_raw.get("content") or raw_payload.get("content")
            if content_text:
                content_text = str(content_text)[:4096]  # Cap length

        # Attachments — strip raw URLs and base64
        raw_attachments = message_raw.get("attachments") or raw_payload.get("attachments") or []
        attachments = [
            _normalize_attachment(att) for att in raw_attachments if isinstance(att, dict)
        ]

        idempotency_key = _make_idempotency_key(event_id, self._tenant.tenant_key)

        return NormalizedChatwootEvent(
            event_type=event_type,
            event_id=event_id,
            correlation_id=corr_id,
            idempotency_key=idempotency_key,
            raw_event_ref=raw_ref,
            account_id_ref=account_id_ref,
            inbox_id=inbox_id,
            inbox_channel_type=inbox_channel_type,
            conversation_id=conversation_id,
            message_id=message_id,
            message_type=message_type,
            sender_type=sender_type,
            content_text=content_text,
            is_private=is_private,
            attachments=attachments,
            tenant_key=self._tenant.tenant_key,
            received_at=now_iso,
        )
