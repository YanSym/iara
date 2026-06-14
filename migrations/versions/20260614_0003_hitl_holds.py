"""HITL holds table — persists Human-in-the-Loop pause records.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create hitl_holds table."""
    op.create_table(
        "hitl_holds",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", sa.String(128), nullable=False, unique=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("conversation_id", sa.String(256), nullable=False),
        sa.Column("thread_id", sa.String(256), nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="pending",
            comment="pending | approved | rejected",
        ),
        sa.Column("resolved_by", sa.String(256)),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_hitl_holds_tenant_status", "hitl_holds", ["tenant_id", "status"])
    op.create_index("ix_hitl_holds_run_id", "hitl_holds", ["run_id"])


def downgrade() -> None:
    """Drop hitl_holds table."""
    op.drop_index("ix_hitl_holds_run_id", "hitl_holds")
    op.drop_index("ix_hitl_holds_tenant_status", "hitl_holds")
    op.drop_table("hitl_holds")
