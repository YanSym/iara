"""add config_data to agent_config_versions.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add config_data JSONB column to agent_config_versions."""
    op.add_column(
        "agent_config_versions",
        sa.Column(
            "config_data",
            JSONB,
            nullable=True,
            comment="Full configuration data for this version",
        ),
    )


def downgrade() -> None:
    """Remove config_data column from agent_config_versions."""
    op.drop_column("agent_config_versions", "config_data")
