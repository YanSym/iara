"""SQLAlchemy ORM models for the IAra runtime.

All tables use tenant scoping columns and appropriate unique constraints
for idempotency. Sensitive data is never stored directly in these models.

Naming conventions: snake_case table names, descriptive column names.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


# ── Runtime operational tables ────────────────────────────────────────────────


class Tenant(Base):
    """Registered tenant with provider account binding."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="chatwoot")
    provider_account_id: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProviderAccount(Base):
    """Provider account (e.g. a Chatwoot account)."""

    __tablename__ = "provider_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id_ref: Mapped[str] = mapped_column(
        String(256), nullable=False, comment="Opaque account ref"
    )
    mcp_base_url: Mapped[str | None] = mapped_column(String(512))
    mcp_credential_ref: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "account_id_ref", name="uq_provider_account"),
    )


class ProviderInbox(Base):
    """Provider inbox (e.g. a WhatsApp inbox in Chatwoot)."""

    __tablename__ = "provider_inboxes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    provider_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("provider_accounts.id"), nullable=False
    )
    inbox_id: Mapped[str] = mapped_column(String(256), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("tenant_id", "inbox_id", name="uq_inbox_per_tenant"),)


class EventReceipt(Base):
    """Idempotency ledger for received webhook events."""

    __tablename__ = "event_receipts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    raw_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="SHA-256 of raw payload"
    )
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_event_receipt"),
        Index("ix_event_receipts_tenant_received", "tenant_id", "received_at"),
    )


class ConversationDebounce(Base):
    """Debounce record per conversation to prevent rapid-fire processing."""

    __tablename__ = "conversation_debounce"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(256), nullable=False)
    locked_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "conversation_id", name="uq_debounce_conversation"),
    )


class ConversationRunLease(Base):
    """Exclusive lease per conversation to prevent concurrent processing."""

    __tablename__ = "conversation_run_leases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(256), nullable=False)
    fencing_token: Mapped[str] = mapped_column(String(128), nullable=False)
    worker_id: Mapped[str] = mapped_column(String(256), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("tenant_id", "conversation_id", name="uq_lease_conversation"),
    )


class AgentRun(Base):
    """Record of a single agent graph execution."""

    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="started")
    flow_version: Mapped[str] = mapped_column(String(64), nullable=False, default="1.0.0")
    config_version_ref: Mapped[str | None] = mapped_column(String(256))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_agent_runs_tenant_correlation", "tenant_id", "correlation_id"),)


class RuntimeRunStep(Base):
    """Individual step within an agent run (per graph node)."""

    __tablename__ = "runtime_run_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id"), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    node_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="Non-sensitive step metadata"
    )


class RuntimeError(Base):
    """Sanitized error record for failed operations."""

    __tablename__ = "runtime_errors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_runs.id"))
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    error_code: Mapped[str] = mapped_column(String(64), nullable=False)
    error_summary: Mapped[str] = mapped_column(Text, nullable=False, comment="Sanitized — no PII")
    node_name: Mapped[str | None] = mapped_column(String(128))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ProviderCommandOutbox(Base):
    """Outbox for pending provider commands (side effects)."""

    __tablename__ = "provider_command_outbox"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    command_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    capability_name: Mapped[str] = mapped_column(String(128), nullable=False)
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    risk_class: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_outbox_idempotency"),
        Index("ix_outbox_pending", "status", "scheduled_at"),
    )


class SafeAuditEvent(Base):
    """Sanitized audit event — no raw data, PII, or secrets."""

    __tablename__ = "safe_audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    conversation_ref: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    counts_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    refs_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(Text, comment="Sanitized — no PII")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("ix_audit_tenant_type", "tenant_id", "event_type", "recorded_at"),)


# ── Semantic memory ───────────────────────────────────────────────────────────


class AgentMemoryItem(Base):
    """Governed semantic memory item for a tenant+namespace.

    Content is always sanitized (no PII). TTL, consent, and LGPD anonymize
    are first-class fields so compliance operations are simple single-row ops.
    """

    __tablename__ = "agent_memory_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    namespace: Mapped[str] = mapped_column(String(512), nullable=False)
    item_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    ttl_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90)
    consent_ref: Mapped[str | None] = mapped_column(String(256))
    is_anonymized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("namespace", "item_key", name="uq_memory_namespace_key"),
        Index("ix_memory_tenant_namespace", "tenant_id", "namespace"),
        Index("ix_memory_expires_at", "expires_at"),
    )


# ── Configuration & publishing tables ─────────────────────────────────────────


class AgentProfile(Base):
    """Agent profile linking a tenant to its published configuration."""

    __tablename__ = "agent_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, unique=True
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    active_publication_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentConfigVersion(Base):
    """Immutable versioned agent configuration draft."""

    __tablename__ = "agent_config_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_profiles.id"), nullable=False)
    version_tag: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="Full configuration data for this version"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("tenant_id", "version_tag", name="uq_config_version_tag"),)


class ConfigPublication(Base):
    """Published (immutable) agent configuration."""

    __tablename__ = "config_publications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    config_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_config_versions.id"), nullable=False
    )
    published_by: Mapped[str] = mapped_column(String(256), nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ── HITL holds ────────────────────────────────────────────────────────────────


class HitlHoldRecord(Base):
    """Postgres-backed record of a paused agent run awaiting human approval."""

    __tablename__ = "hitl_holds"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(256), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(256), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", comment="pending | approved | rejected"
    )
    resolved_by: Mapped[str | None] = mapped_column(String(256))
    context_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="Non-sensitive graph state snapshot"
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_hitl_holds_tenant_status", "tenant_id", "status"),
        Index("ix_hitl_holds_run_id_idx", "run_id"),
    )


# ── Follow-up queue ───────────────────────────────────────────────────────────


class FollowUpQueueItem(Base):
    """Durable queue of scheduled follow-up messages.

    The follow-up scheduler worker polls this table and enqueues due items
    to the provider_command_outbox at trigger_at. All content is stored as
    opaque hashes — no raw message text or PII (INV-05).
    """

    __tablename__ = "follow_up_queue"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(256), nullable=False)
    contact_ref: Mapped[str] = mapped_column(
        String(256), nullable=False, comment="Hashed contact ref"
    )
    message_ref: Mapped[str] = mapped_column(
        String(256), nullable=False, comment="SHA-256 of message"
    )
    message_length: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_ref: Mapped[str] = mapped_column(
        String(256), nullable=False, comment="SHA-256 of reason"
    )
    trigger_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", comment="pending | sent | skipped | failed"
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    opted_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    skip_reason: Mapped[str | None] = mapped_column(String(256))

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_follow_up_idempotency"),
        Index(
            "ix_follow_up_trigger",
            "trigger_at",
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_follow_up_tenant_status", "tenant_id", "status"),
    )
