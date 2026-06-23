"""Add follow_up_queue table and context_snapshot column to hitl_holds.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add context_snapshot to hitl_holds; create follow_up_queue table."""
    # Add context_snapshot column to existing hitl_holds table
    op.add_column(
        "hitl_holds",
        sa.Column("context_snapshot", JSONB, nullable=True,
                  comment="Non-sensitive graph state snapshot"),
    )

    # Create follow_up_queue table
    op.create_table(
        "follow_up_queue",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True),
                  sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("conversation_id", sa.String(256), nullable=False),
        sa.Column("contact_ref", sa.String(256), nullable=False,
                  comment="Hashed contact ref"),
        sa.Column("message_ref", sa.String(256), nullable=False,
                  comment="SHA-256 of message"),
        sa.Column("message_length", sa.Integer, nullable=False),
        sa.Column("reason_ref", sa.String(256), nullable=False,
                  comment="SHA-256 of reason"),
        sa.Column("trigger_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False,
                  server_default="pending",
                  comment="pending | sent | skipped | failed"),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
        sa.Column("opted_out", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()")),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("skip_reason", sa.String(256), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_follow_up_idempotency"),
    )

    op.create_index(
        "ix_follow_up_trigger",
        "follow_up_queue",
        ["trigger_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_follow_up_tenant_status",
        "follow_up_queue",
        ["tenant_id", "status"],
    )


def downgrade() -> None:
    """Remove follow_up_queue; drop context_snapshot from hitl_holds."""
    op.drop_index("ix_follow_up_tenant_status", "follow_up_queue")
    op.drop_index("ix_follow_up_trigger", "follow_up_queue")
    op.drop_table("follow_up_queue")
    op.drop_column("hitl_holds", "context_snapshot")
