"""Add whatsapp_inbox_template table.

Revision ID: 002
Revises: 001
Create Date: 2026-04-14

Creates table: whatsapp_inbox_template.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_inbox_template",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("hub_id", sa.Uuid(), nullable=False, index=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column(
            "is_deleted",
            sa.Boolean(),
            server_default="false",
            nullable=False,
            index=True,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("language", sa.String(10), server_default="es"),
        sa.Column("category", sa.String(20), server_default="UTILITY"),
        sa.Column("header", sa.Text(), server_default=""),
        sa.Column("body", sa.Text(), server_default=""),
        sa.Column("footer", sa.Text(), server_default=""),
        sa.Column("meta_template_id", sa.String(100), server_default=""),
        sa.Column("meta_status", sa.String(20), server_default="pending"),
        sa.Column(
            "variables", postgresql.JSONB(), server_default="[]", nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
    )
    op.create_index(
        "ix_wa_template_hub",
        "whatsapp_inbox_template",
        ["hub_id"],
    )
    op.create_index(
        "ix_wa_template_hub_name",
        "whatsapp_inbox_template",
        ["hub_id", "name"],
    )
    op.create_index(
        "ix_wa_template_hub_status",
        "whatsapp_inbox_template",
        ["hub_id", "meta_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_wa_template_hub_status", "whatsapp_inbox_template")
    op.drop_index("ix_wa_template_hub_name", "whatsapp_inbox_template")
    op.drop_index("ix_wa_template_hub", "whatsapp_inbox_template")
    op.drop_table("whatsapp_inbox_template")
