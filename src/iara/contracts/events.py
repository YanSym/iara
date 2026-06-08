"""Event contracts — incoming webhook payloads normalized for the runtime.

Sensitive fields (raw body, phone numbers, private content) are marked with
``exclude=True`` in ``model_dump()`` so they never appear in logs or audit events.

The ``RawEventRef`` stores only a hash of the raw payload — the payload itself
is never persisted.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel, Field


class EventType(StrEnum):
    """Chatwoot webhook event types."""

    MESSAGE_CREATED = "message_created"
    MESSAGE_UPDATED = "message_updated"
    CONVERSATION_CREATED = "conversation_created"
    CONVERSATION_UPDATED = "conversation_updated"
    CONVERSATION_STATUS_CHANGED = "conversation_status_changed"
    CONTACT_CREATED = "contact_created"
    CONTACT_UPDATED = "contact_updated"


class MessageType(StrEnum):
    """Message direction within a Chatwoot conversation."""

    INCOMING = "incoming"
    OUTGOING = "outgoing"
    ACTIVITY = "activity"
    TEMPLATE = "template"


class SenderType(StrEnum):
    """Who sent the message."""

    CONTACT = "contact"
    AGENT_BOT = "agent_bot"
    USER = "user"
    SYSTEM = "system"


class AttachmentType(StrEnum):
    """Types of message attachments."""

    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    FILE = "file"
    LOCATION = "location"
    FALLBACK = "fallback"
    ARTICLE = "article"
    CONTACT = "contact"


class EligibilityReason(StrEnum):
    """Reason code for an eligibility decision."""

    ACCEPTED = "accepted"
    OUTGOING_MESSAGE = "outgoing_message"
    BOT_SENDER = "bot_sender"
    SYSTEM_SENDER = "system_sender"
    PRIVATE_NOTE = "private_note"
    ACCOUNT_MISMATCH = "account_mismatch"
    INBOX_NOT_CONFIGURED = "inbox_not_configured"
    TENANT_SUSPENDED = "tenant_suspended"
    DUPLICATE_EVENT = "duplicate_event"
    DEBOUNCE_ACTIVE = "debounce_active"
    UNSUPPORTED_EVENT_TYPE = "unsupported_event_type"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    ENRICHMENT_REQUIRED = "enrichment_required"


class RawEventRef(BaseModel):
    """A secure reference to a raw webhook payload.

    The raw payload is NEVER stored. Only the hash is kept for deduplication
    and audit trail purposes.

    Attributes:
        raw_hash: SHA-256 hex digest of the raw payload bytes.
        received_at: ISO timestamp when the webhook was received.
        source_ip_ref: Opaque ref to the source IP (not the IP itself).
        byte_count: Size of the raw payload in bytes.
    """

    raw_hash: str = Field(description="SHA-256 hex digest of raw payload")
    received_at: str = Field(description="ISO 8601 timestamp of receipt")
    source_ip_ref: str | None = Field(default=None, description="Opaque ref — not the real IP")
    byte_count: int = Field(default=0, ge=0)

    @classmethod
    def from_raw_bytes(
        cls, raw: bytes, received_at: str, source_ip_ref: str | None = None
    ) -> RawEventRef:
        """Create a RawEventRef from raw bytes without storing them.

        Args:
            raw: The raw webhook payload bytes.
            received_at: ISO 8601 receipt timestamp.
            source_ip_ref: Optional opaque reference (not the real IP).

        Returns:
            RawEventRef: Reference containing only the hash and metadata.
        """
        return cls(
            raw_hash=hashlib.sha256(raw).hexdigest(),
            received_at=received_at,
            source_ip_ref=source_ip_ref,
            byte_count=len(raw),
        )


class CanonicalAttachment(BaseModel):
    """Canonical representation of a message attachment.

    The raw file bytes, base64 data, and temporary URLs are NEVER stored here.
    Only metadata and a safe reference are kept.

    Attributes:
        attachment_type: Type of attachment (image, audio, file, etc.).
        content_type: MIME type of the attachment.
        file_name: Original file name (may be sanitized).
        file_size_bytes: File size for media understanding decisions.
        attachment_ref: Opaque reference for the media pipeline (not a URL).
        is_processed: Whether the media understanding subgraph has processed this.
        processing_result: Summary of processing result (text transcript, description, etc.).
    """

    attachment_type: AttachmentType
    content_type: str | None = None
    file_name: str | None = None
    file_size_bytes: int | None = None
    attachment_ref: str = Field(description="Opaque ref for media pipeline — not a raw URL")
    is_processed: bool = False
    processing_result: str | None = Field(
        default=None,
        description="Sanitized processing result (transcript, description, extracted text)",
    )

    # These fields hold sensitive data and are EXCLUDED from model_dump by default
    # They are only used transiently during processing and must never be persisted.
    _raw_url: str | None = None  # temporary download URL — never persisted
    _raw_base64: str | None = None  # raw base64 — never persisted


class NormalizedChatwootEvent(BaseModel):
    """Normalized, sanitized representation of a Chatwoot webhook event.

    This model is the boundary between the raw provider payload and the runtime.
    All sensitive fields (raw body, phone numbers, private content) are excluded
    from serialization to prevent leakage into logs, state, or audit events.

    Attributes:
        event_type: The type of Chatwoot event.
        event_id: Unique event identifier from Chatwoot.
        correlation_id: Runtime correlation ID for tracing.
        idempotency_key: Key for deduplication in the ledger.
        raw_event_ref: Reference to the raw payload hash (not the payload).
        account_id_ref: Opaque reference to the account (not the real ID).
        inbox_id: Inbox identifier within the provider account.
        inbox_channel_type: Source channel type (WhatsApp, Instagram, etc.).
        conversation_id: Conversation identifier.
        message_id: Message identifier (for message events).
        message_type: Direction of the message.
        sender_type: Who sent the message.
        content_text: Sanitized text content of the message.
        is_private: Whether the message is a private note.
        attachments: List of canonical attachment references.
        tenant_key: Tenant key from the webhook URL path.
        received_at: ISO 8601 timestamp of receipt.
    """

    event_type: EventType
    event_id: str = Field(description="Chatwoot event UUID")
    correlation_id: str = Field(description="Runtime correlation ID for distributed tracing")
    idempotency_key: str = Field(description="Unique key for deduplication")
    raw_event_ref: RawEventRef = Field(description="Hash-only reference to the raw payload")
    account_id_ref: str = Field(description="Opaque account reference — not the real account ID")
    inbox_id: str = Field(description="Inbox identifier within the provider account")
    inbox_channel_type: str = Field(description="Source channel (whatsapp, instagram, email, etc.)")
    conversation_id: str = Field(description="Conversation identifier")
    message_id: str | None = Field(
        default=None, description="Message identifier for message events"
    )
    message_type: MessageType | None = None
    sender_type: SenderType | None = None
    content_text: str | None = Field(
        default=None,
        description="Sanitized message text content",
    )
    is_private: bool = Field(
        default=False, description="True for private notes — excluded from context"
    )
    attachments: list[CanonicalAttachment] = Field(default_factory=list)
    tenant_key: str = Field(description="Tenant key from webhook URL path")
    received_at: str = Field(description="ISO 8601 receipt timestamp")

    # Private-note content is redacted from serialization (exclude in model_dump)
    _private_content: str | None = None  # Never serialized — internal guard only


# Alias per the spec
CanonicalMessageEvent = NormalizedChatwootEvent


class EligibilityDecision(BaseModel):
    """Decision about whether a normalized event should be processed by the runtime.

    Attributes:
        eligible: Whether the event should proceed to the agent graph.
        reason: Machine-readable reason code.
        details: Human-readable details (sanitized, no raw data).
        enrichment_needed: If True, a read-only enrichment call is needed before processing.
        enrichment_fields: Fields that need enrichment (e.g. ``["inbox_channel_type"]``).
    """

    eligible: bool
    reason: EligibilityReason
    details: str = Field(default="", description="Sanitized explanation")
    enrichment_needed: bool = False
    enrichment_fields: list[str] = Field(default_factory=list)

    @classmethod
    def accept(cls) -> EligibilityDecision:
        """Create an accept decision."""
        return cls(eligible=True, reason=EligibilityReason.ACCEPTED)

    @classmethod
    def reject(cls, reason: EligibilityReason, details: str = "") -> EligibilityDecision:
        """Create a rejection decision.

        Args:
            reason: Machine-readable rejection reason.
            details: Sanitized human-readable details.

        Returns:
            EligibilityDecision: Rejection decision.
        """
        return cls(eligible=False, reason=reason, details=details)

    @classmethod
    def needs_enrichment(cls, fields: list[str]) -> EligibilityDecision:
        """Create a decision that requires read-only enrichment before processing.

        Args:
            fields: List of field names that need enrichment.

        Returns:
            EligibilityDecision: Decision requiring enrichment.
        """
        return cls(
            eligible=False,
            reason=EligibilityReason.ENRICHMENT_REQUIRED,
            enrichment_needed=True,
            enrichment_fields=fields,
        )
