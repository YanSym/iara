"""Audit and evidence contracts.

``SanitizedEvidence`` is the only permitted format for audit records and gate
evidence reports. It contains ONLY hashes, refs, counts, statuses, and sanitized
error codes — never raw data, PII, tokens, or payloads.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AuditEventType(StrEnum):
    """Types of auditable events in the runtime."""

    WEBHOOK_RECEIVED = "webhook_received"
    ELIGIBILITY_CHECKED = "eligibility_checked"
    JOB_QUEUED = "job_queued"
    LEASE_ACQUIRED = "lease_acquired"
    LEASE_RELEASED = "lease_released"
    GRAPH_STARTED = "graph_started"
    GRAPH_COMPLETED = "graph_completed"
    GRAPH_FAILED = "graph_failed"
    GRAPH_RESUMED = "graph_resumed"
    MEDIA_PROCESSED = "media_processed"
    TOOL_INVOKED = "tool_invoked"
    TOOL_COMPLETED = "tool_completed"
    TOOL_BLOCKED = "tool_blocked"
    COMMAND_QUEUED = "command_queued"
    COMMAND_SENT = "command_sent"
    COMMAND_CONFIRMED = "command_confirmed"
    COMMAND_FAILED = "command_failed"
    READBACK_CONFIRMED = "readback_confirmed"
    READBACK_FAILED = "readback_failed"
    HITL_REQUESTED = "hitl_requested"
    HITL_APPROVED = "hitl_approved"
    HITL_REJECTED = "hitl_rejected"
    CONFIG_PUBLISHED = "config_published"
    CONFIG_ROLLED_BACK = "config_rolled_back"
    CROSS_TENANT_BLOCKED = "cross_tenant_blocked"
    FAIL_CLOSED_TRIGGERED = "fail_closed_triggered"


class SanitizedEvidence(BaseModel):
    """Sanitized evidence record for audit trails and gate reports.

    This is the ONLY permitted format for recording what happened. It contains:
    - Hashes (SHA-256 refs) of input/output
    - Counts (message count, attachment count, etc.)
    - Statuses (success, failed, blocked, etc.)
    - Sanitized error codes (no stack traces, no raw messages)
    - Refs (opaque references to tenants, conversations, commands)

    It NEVER contains:
    - Raw payloads, phone numbers, real account IDs
    - Tokens, credentials, headers, cookies
    - Private notes or conversation content
    - Base64, temporary URLs, raw attachments

    Attributes:
        event_type: Type of audit event.
        correlation_id: Distributed tracing ID.
        tenant_ref: Opaque tenant reference (not the real key).
        conversation_ref: Opaque conversation reference.
        input_hash: SHA-256 hash of the relevant input.
        output_ref: Opaque output reference.
        status: Outcome status.
        counts: Sanitized counts (message_count, attachment_count, etc.).
        refs: Additional opaque references.
        error_code: Sanitized error code if applicable.
        error_summary: Sanitized error summary (no PII or raw details).
        metadata: Additional non-sensitive metadata.
        recorded_at: ISO 8601 timestamp.
    """

    event_type: AuditEventType
    correlation_id: str
    tenant_ref: str = Field(description="Opaque tenant reference")
    conversation_ref: str | None = None
    input_hash: str | None = Field(default=None, description="SHA-256 hash of input")
    output_ref: str | None = Field(default=None, description="Opaque output reference")
    status: str = Field(description="Outcome: success | failed | blocked | partial | pending")
    counts: dict[str, int] = Field(default_factory=dict, description="Sanitized counts only")
    refs: dict[str, str] = Field(default_factory=dict, description="Additional opaque references")
    error_code: str | None = None
    error_summary: str | None = Field(
        default=None,
        description="Sanitized error summary — no PII, raw messages, or stack traces",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Non-sensitive metadata (versions, modes, flags)",
    )
    recorded_at: str = Field(description="ISO 8601 timestamp")

    @classmethod
    def for_event(
        cls,
        event_type: AuditEventType,
        *,
        correlation_id: str,
        tenant_ref: str,
        status: str,
        recorded_at: str,
        **kwargs: Any,
    ) -> SanitizedEvidence:
        """Convenience constructor for creating a sanitized evidence record.

        Args:
            event_type: The type of event.
            correlation_id: Distributed tracing ID.
            tenant_ref: Opaque tenant reference.
            status: Outcome status string.
            recorded_at: ISO 8601 timestamp.
            **kwargs: Additional optional fields.

        Returns:
            SanitizedEvidence: A new sanitized evidence record.
        """
        return cls(
            event_type=event_type,
            correlation_id=correlation_id,
            tenant_ref=tenant_ref,
            status=status,
            recorded_at=recorded_at,
            **kwargs,
        )
