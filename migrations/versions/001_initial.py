"""Initial whatsapp_inbox module schema.

Revision ID: 001
Revises: -
Create Date: 2026-04-04

Creates tables: whatsapp_inbox_settings, whatsapp_inbox_employee_link,
whatsapp_inbox_conversation, whatsapp_inbox_message, whatsapp_inbox_request.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # WhatsAppInboxSettings
    op.create_table(
        "whatsapp_inbox_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("hub_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False, index=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), server_default="false"),
        sa.Column("account_mode", sa.String(15), server_default="shared"),
        sa.Column("auto_reply_enabled", sa.Boolean(), server_default="true"),
        sa.Column("approval_mode", sa.String(10), server_default="auto"),
        sa.Column("require_confirmation", sa.Boolean(), server_default="true"),
        sa.Column("request_schema", postgresql.JSONB(), server_default="{}"),
        sa.Column("gpt_system_prompt", sa.Text(), server_default=""),
        sa.Column("input_modules", postgresql.JSONB(), server_default="[]"),
        sa.Column("output_modules", postgresql.JSONB(), server_default="[]"),
        sa.Column("auto_close_hours", sa.Integer(), server_default="24"),
        sa.Column("notify_staff_new_request", sa.Boolean(), server_default="true"),
        sa.Column("greeting_message", sa.Text(), server_default=""),
        sa.Column("out_of_hours_message", sa.Text(), server_default=""),
        sa.UniqueConstraint("hub_id", name="unique_wa_inbox_settings_per_hub"),
    )

    # EmployeeWhatsAppLink
    op.create_table(
        "whatsapp_inbox_employee_link",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("hub_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False, index=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("employee_id", sa.Uuid(), sa.ForeignKey("local_user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("phone_number_id", sa.String(50), nullable=False, index=True),
        sa.Column("display_phone", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.UniqueConstraint("hub_id", "phone_number_id", name="unique_wa_phone_per_hub"),
    )

    # WhatsAppConversation
    op.create_table(
        "whatsapp_inbox_conversation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("hub_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False, index=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("customer_id", sa.Uuid(), sa.ForeignKey("customers_customer.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assigned_to_id", sa.Uuid(), sa.ForeignKey("local_user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("phone_number_id", sa.String(50), server_default=""),
        sa.Column("wa_contact_id", sa.String(50), nullable=False, index=True),
        sa.Column("contact_name", sa.String(200), nullable=False),
        sa.Column("contact_phone", sa.String(50), nullable=False),
        sa.Column("status", sa.String(25), server_default="active"),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("context", postgresql.JSONB(), server_default="{}"),
        sa.Column("unread_count", sa.Integer(), server_default="0"),
    )
    op.create_index("ix_wa_conv_hub_contact", "whatsapp_inbox_conversation", ["hub_id", "wa_contact_id"])
    op.create_index("ix_wa_conv_hub_status", "whatsapp_inbox_conversation", ["hub_id", "status"])
    op.create_index("ix_wa_conv_hub_assigned", "whatsapp_inbox_conversation", ["hub_id", "assigned_to_id"])

    # WhatsAppMessage
    op.create_table(
        "whatsapp_inbox_message",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("hub_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False, index=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("whatsapp_inbox_conversation.id", ondelete="CASCADE"), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("wa_message_id", sa.String(100), nullable=False, index=True),
        sa.Column("message_type", sa.String(20), server_default="text"),
        sa.Column("body", sa.Text(), server_default=""),
        sa.Column("media_url", sa.String(500), server_default=""),
        sa.Column("status", sa.String(20), server_default="received"),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
    )
    op.create_index("ix_wa_msg_hub_wamsgid", "whatsapp_inbox_message", ["hub_id", "wa_message_id"])

    # InboxRequest
    op.create_table(
        "whatsapp_inbox_request",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("hub_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False, index=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("whatsapp_inbox_conversation.id", ondelete="CASCADE"), nullable=False),
        sa.Column("customer_id", sa.Uuid(), sa.ForeignKey("customers_customer.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reference_number", sa.String(30), nullable=False, index=True),
        sa.Column("request_type", sa.String(20), server_default="custom"),
        sa.Column("status", sa.String(20), server_default="pending_review", index=True),
        sa.Column("data", postgresql.JSONB(), server_default="{}"),
        sa.Column("raw_summary", sa.Text(), server_default=""),
        sa.Column("confidence_score", sa.Float(), server_default="0.0"),
        sa.Column("notes", sa.Text(), server_default=""),
        sa.Column("assigned_to_id", sa.Uuid(), sa.ForeignKey("local_user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("linked_module", sa.String(50), server_default=""),
        sa.Column("linked_object_id", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_wa_req_hub_status", "whatsapp_inbox_request", ["hub_id", "status"])
    op.create_index("ix_wa_req_hub_type", "whatsapp_inbox_request", ["hub_id", "request_type"])


def downgrade() -> None:
    op.drop_table("whatsapp_inbox_request")
    op.drop_table("whatsapp_inbox_message")
    op.drop_table("whatsapp_inbox_conversation")
    op.drop_table("whatsapp_inbox_employee_link")
    op.drop_table("whatsapp_inbox_settings")
