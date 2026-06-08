"""Initial schema — runtime operational tables.

Revision ID: 0001
Revises:
Create Date: 2026-06-05

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create initial runtime operational tables."""

    # tenants
    op.create_table(
        "tenants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_key", sa.String(128), nullable=False, unique=True),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("provider", sa.String(64), nullable=False, server_default="chatwoot"),
        sa.Column("provider_account_id", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_tenants_tenant_key", "tenants", ["tenant_key"])

    # provider_accounts
    op.create_table(
        "provider_accounts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("account_id_ref", sa.String(256), nullable=False, comment="Opaque account ref"),
        sa.Column("mcp_base_url", sa.String(512)),
        sa.Column("mcp_credential_ref", sa.String(512), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "provider", "account_id_ref", name="uq_provider_account"),
    )

    # provider_inboxes
    op.create_table(
        "provider_inboxes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "provider_account_id",
            UUID(as_uuid=True),
            sa.ForeignKey("provider_accounts.id"),
            nullable=False,
        ),
        sa.Column("inbox_id", sa.String(256), nullable=False),
        sa.Column("channel_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "inbox_id", name="uq_inbox_per_tenant"),
    )

    # event_receipts
    op.create_table(
        "event_receipts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("raw_hash", sa.String(64), nullable=False, comment="SHA-256 of raw payload"),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="received"),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_event_receipt"),
    )
    op.create_index(
        "ix_event_receipts_tenant_received", "event_receipts", ["tenant_id", "received_at"]
    )

    # conversation_debounce
    op.create_table(
        "conversation_debounce",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("conversation_id", sa.String(256), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "conversation_id", name="uq_debounce_conversation"),
    )

    # conversation_run_leases
    op.create_table(
        "conversation_run_leases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("conversation_id", sa.String(256), nullable=False),
        sa.Column("fencing_token", sa.String(128), nullable=False),
        sa.Column("worker_id", sa.String(256), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "conversation_id", name="uq_lease_conversation"),
    )

    # agent_runs
    op.create_table(
        "agent_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("conversation_id", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="started"),
        sa.Column("flow_version", sa.String(64), nullable=False, server_default="1.0.0"),
        sa.Column("config_version_ref", sa.String(256)),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_agent_runs_tenant_correlation", "agent_runs", ["tenant_id", "correlation_id"]
    )

    # runtime_run_steps
    op.create_table(
        "runtime_run_steps",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("agent_runs.id"), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("node_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("metadata_json", JSONB, comment="Non-sensitive step metadata"),
    )

    # runtime_errors
    op.create_table(
        "runtime_errors",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("agent_runs.id")),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=False),
        sa.Column("error_summary", sa.Text, nullable=False, comment="Sanitized — no PII"),
        sa.Column("node_name", sa.String(128)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # provider_command_outbox
    op.create_table(
        "provider_command_outbox",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("command_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("capability_name", sa.String(128), nullable=False),
        sa.Column("parameters_json", JSONB, nullable=False),
        sa.Column("risk_class", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_outbox_idempotency"),
    )
    op.create_index("ix_outbox_pending", "provider_command_outbox", ["status", "scheduled_at"])

    # safe_audit_events
    op.create_table(
        "safe_audit_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("conversation_ref", sa.String(256)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("counts_json", JSONB, nullable=False, server_default="{}"),
        sa.Column("refs_json", JSONB, nullable=False, server_default="{}"),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_summary", sa.Text, comment="Sanitized — no PII"),
        sa.Column("metadata_json", JSONB, nullable=False, server_default="{}"),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_audit_tenant_type", "safe_audit_events", ["tenant_id", "event_type", "recorded_at"]
    )

    # agent_profiles
    op.create_table(
        "agent_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("active_publication_id", UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # agent_config_versions
    op.create_table(
        "agent_config_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "profile_id", UUID(as_uuid=True), sa.ForeignKey("agent_profiles.id"), nullable=False
        ),
        sa.Column("version_tag", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "version_tag", name="uq_config_version_tag"),
    )

    # config_publications
    op.create_table(
        "config_publications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "config_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_config_versions.id"),
            nullable=False,
        ),
        sa.Column("published_by", sa.String(256), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    """Drop all initial tables."""
    op.drop_table("config_publications")
    op.drop_table("agent_config_versions")
    op.drop_table("agent_profiles")
    op.drop_index("ix_audit_tenant_type", "safe_audit_events")
    op.drop_table("safe_audit_events")
    op.drop_index("ix_outbox_pending", "provider_command_outbox")
    op.drop_table("provider_command_outbox")
    op.drop_table("runtime_errors")
    op.drop_table("runtime_run_steps")
    op.drop_index("ix_agent_runs_tenant_correlation", "agent_runs")
    op.drop_table("agent_runs")
    op.drop_table("conversation_run_leases")
    op.drop_table("conversation_debounce")
    op.drop_index("ix_event_receipts_tenant_received", "event_receipts")
    op.drop_table("event_receipts")
    op.drop_table("provider_inboxes")
    op.drop_table("provider_accounts")
    op.drop_index("ix_tenants_tenant_key", "tenants")
    op.drop_table("tenants")
