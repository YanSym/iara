"""agent_memory_items table + command_requester_bindings table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-08

Tables added:
- agent_memory_items: Durable, TTL-governed semantic memory for GovernedMemoryStore
- command_requester_bindings: Authorization table for admin command senders (Phase 8)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create agent_memory_items and command_requester_bindings tables."""

    # agent_memory_items — governed semantic memory
    op.create_table(
        "agent_memory_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("namespace", sa.String(512), nullable=False),
        sa.Column("item_key", sa.String(512), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("ttl_days", sa.Integer, nullable=False, server_default="90"),
        sa.Column("consent_ref", sa.String(256)),
        sa.Column("is_anonymized", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_unique_constraint(
        "uq_memory_namespace_key",
        "agent_memory_items",
        ["namespace", "item_key"],
    )
    op.create_index(
        "ix_memory_tenant_namespace",
        "agent_memory_items",
        ["tenant_id", "namespace"],
    )
    op.create_index(
        "ix_memory_expires_at",
        "agent_memory_items",
        ["expires_at"],
    )

    # command_requester_bindings — Phase 8 authorization table
    op.create_table(
        "command_requester_bindings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("sender_type", sa.String(64), nullable=False),
        sa.Column("sender_ref", sa.String(256)),
        sa.Column("allowed_commands", sa.Text, nullable=False, server_default="*"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_cmd_binding_tenant_type",
        "command_requester_bindings",
        ["tenant_id", "sender_type"],
    )


def downgrade() -> None:
    """Drop agent_memory_items and command_requester_bindings tables."""
    op.drop_table("command_requester_bindings")
    op.drop_index("ix_memory_expires_at", table_name="agent_memory_items")
    op.drop_index("ix_memory_tenant_namespace", table_name="agent_memory_items")
    op.drop_constraint("uq_memory_namespace_key", "agent_memory_items")
    op.drop_table("agent_memory_items")
