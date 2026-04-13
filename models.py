"""WhatsApp Inbox models — SQLAlchemy 2.0.

Models:
- WhatsAppInboxSettings — per-hub configuration (singleton)
- EmployeeWhatsAppLink — maps employee to their WhatsApp number (per-employee mode)
- WhatsAppConversation — conversation with a WhatsApp contact
- WhatsAppMessage — individual message in a conversation
- InboxRequest — parsed request from conversation (dynamic schema)
"""

from __future__ import annotations

import json

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from uuid import UUID

from app.core.db.base import HubBaseModel

from datetime import datetime


# ==============================================================================
# SETTINGS
# ==============================================================================

class WhatsAppInboxSettings(HubBaseModel):
    """Per-hub WhatsApp Inbox settings (singleton per hub)."""

    __tablename__ = "whatsapp_inbox_settings"
    __table_args__ = (
        UniqueConstraint("hub_id", name="unique_wa_inbox_settings_per_hub"),
        {"extend_existing": True},
    )

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Account mode
    account_mode: Mapped[str] = mapped_column(
        String(15), default="shared", server_default="shared",
    )

    # Auto-reply
    auto_reply_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true",
    )

    # Approval mode
    approval_mode: Mapped[str] = mapped_column(
        String(10), default="auto", server_default="auto",
    )
    require_confirmation: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true",
    )

    # Dynamic request schema
    request_schema: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default="{}",
    )

    # GPT configuration
    gpt_system_prompt: Mapped[str] = mapped_column(Text, default="", server_default="")

    # Module integration
    input_modules: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]",
    )
    output_modules: Mapped[list] = mapped_column(
        JSONB, default=list, server_default="[]",
    )

    # Auto-close
    auto_close_hours: Mapped[int] = mapped_column(
        Integer, default=24, server_default="24",
    )

    # Notifications
    notify_staff_new_request: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true",
    )

    # Auto messages
    greeting_message: Mapped[str] = mapped_column(Text, default="", server_default="")
    out_of_hours_message: Mapped[str] = mapped_column(Text, default="", server_default="")

    def __repr__(self) -> str:
        return f"WhatsAppInboxSettings(hub_id={self.hub_id})"


# ==============================================================================
# EMPLOYEE WHATSAPP LINK (per-employee mode)
# ==============================================================================

class EmployeeWhatsAppLink(HubBaseModel):
    """Maps a LocalUser (employee) to their WhatsApp phone_number_id.

    Used in per-employee mode: each salesperson connects their own
    WhatsApp Business number.
    """

    __tablename__ = "whatsapp_inbox_employee_link"
    __table_args__ = (
        UniqueConstraint("hub_id", "phone_number_id", name="unique_wa_phone_per_hub"),
        {"extend_existing": True},
    )

    employee_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("local_user.id", ondelete="CASCADE"), nullable=False,
    )
    phone_number_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    display_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    def __repr__(self) -> str:
        return f"EmployeeWhatsAppLink(employee_id={self.employee_id}, phone={self.display_phone})"


# ==============================================================================
# CONVERSATION
# ==============================================================================

class WhatsAppConversation(HubBaseModel):
    """A conversation with a WhatsApp contact."""

    __tablename__ = "whatsapp_inbox_conversation"
    __table_args__ = (
        Index("ix_wa_conv_hub_contact", "hub_id", "wa_contact_id"),
        Index("ix_wa_conv_hub_status", "hub_id", "status"),
        Index("ix_wa_conv_hub_assigned", "hub_id", "assigned_to_id"),
        {"extend_existing": True},
    )

    customer_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("customers_customer.id", ondelete="SET NULL"), nullable=True,
    )
    assigned_to_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("local_user.id", ondelete="SET NULL"), nullable=True,
    )
    phone_number_id: Mapped[str] = mapped_column(String(50), default="", server_default="")
    wa_contact_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    contact_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_phone: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(25), default="active", server_default="active")
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    context: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    unread_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Relationships
    messages: Mapped[list[WhatsAppMessage]] = relationship(
        "WhatsAppMessage", back_populates="conversation", lazy="selectin",
        cascade="all, delete-orphan",
    )
    requests: Mapped[list[InboxRequest]] = relationship(
        "InboxRequest", back_populates="conversation", lazy="selectin",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"WhatsAppConversation({self.contact_name}, {self.wa_contact_id})"


# ==============================================================================
# MESSAGE
# ==============================================================================

class WhatsAppMessage(HubBaseModel):
    """Individual message in a WhatsApp conversation."""

    __tablename__ = "whatsapp_inbox_message"
    __table_args__ = (
        Index("ix_wa_msg_hub_wamsgid", "hub_id", "wa_message_id"),
        {"extend_existing": True},
    )

    conversation_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("whatsapp_inbox_conversation.id", ondelete="CASCADE"), nullable=False,
    )
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    wa_message_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    message_type: Mapped[str] = mapped_column(String(20), default="text", server_default="text")
    body: Mapped[str] = mapped_column(Text, default="", server_default="")
    media_url: Mapped[str] = mapped_column(String(500), default="", server_default="")
    status: Mapped[str] = mapped_column(String(20), default="received", server_default="received")
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, server_default="{}")

    # Relationships
    conversation: Mapped[WhatsAppConversation] = relationship(
        "WhatsAppConversation", back_populates="messages", lazy="joined",
    )

    def __repr__(self) -> str:
        prefix = ">" if self.direction == "outbound" else "<"
        return f"WhatsAppMessage({prefix} {self.body[:50]})" if self.body else f"WhatsAppMessage({prefix} [{self.message_type}])"


# ==============================================================================
# INBOX REQUEST (dynamic schema)
# ==============================================================================

class InboxRequest(HubBaseModel):
    """Parsed request from a WhatsApp conversation.

    The ``data`` JSONB field holds structured data parsed by GPT,
    following the ``request_schema`` defined in WhatsAppInboxSettings.
    """

    __tablename__ = "whatsapp_inbox_request"
    __table_args__ = (
        Index("ix_wa_req_hub_status", "hub_id", "status"),
        Index("ix_wa_req_hub_type", "hub_id", "request_type"),
        {"extend_existing": True},
    )

    REQUEST_TYPE_CHOICES = ["order", "reservation", "appointment", "quote", "transport", "custom"]
    STATUS_CHOICES = ["pending_review", "confirmed", "rejected", "fulfilled", "cancelled"]

    conversation_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("whatsapp_inbox_conversation.id", ondelete="CASCADE"), nullable=False,
    )
    customer_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("customers_customer.id", ondelete="SET NULL"), nullable=True,
    )

    reference_number: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    request_type: Mapped[str] = mapped_column(String(20), default="custom", server_default="custom")
    status: Mapped[str] = mapped_column(
        String(20), default="pending_review", server_default="pending_review", index=True,
    )

    # Dynamic data
    data: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    raw_summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0")

    # Staff
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    assigned_to_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("local_user.id", ondelete="SET NULL"), nullable=True,
    )

    # Link to created object in another module
    linked_module: Mapped[str] = mapped_column(String(50), default="", server_default="")
    linked_object_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    # Timestamps
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    conversation: Mapped[WhatsAppConversation] = relationship(
        "WhatsAppConversation", back_populates="requests", lazy="joined",
    )

    def __repr__(self) -> str:
        return f"InboxRequest({self.reference_number}, {self.request_type}, {self.status})"

    @property
    def confidence_percent(self) -> int:
        """Confidence as integer percentage (0-100)."""
        return int(self.confidence_score * 100) if self.confidence_score else 0

    @property
    def data_pretty(self) -> str:
        """JSON-formatted data for display."""
        return json.dumps(self.data, indent=2, ensure_ascii=False) if self.data else "{}"

    @property
    def status_class(self) -> str:
        return {
            "pending_review": "warning",
            "confirmed": "primary",
            "rejected": "error",
            "fulfilled": "success",
            "cancelled": "neutral",
        }.get(self.status, "neutral")

    @property
    def request_type_display(self) -> str:
        return {
            "order": "Order",
            "reservation": "Reservation",
            "appointment": "Appointment",
            "quote": "Quote",
            "transport": "Transport",
            "custom": "Custom",
        }.get(self.request_type, self.request_type)

    @property
    def status_display(self) -> str:
        return {
            "pending_review": "Pending Review",
            "confirmed": "Confirmed",
            "rejected": "Rejected",
            "fulfilled": "Fulfilled",
            "cancelled": "Cancelled",
        }.get(self.status, self.status)


# ==============================================================================
# WHATSAPP TEMPLATE
# ==============================================================================

class WhatsAppTemplate(HubBaseModel):
    """WhatsApp Business message template (Meta-approved)."""

    __tablename__ = "whatsapp_inbox_template"
    __table_args__ = (
        Index("ix_wa_template_hub", "hub_id"),
        Index("ix_wa_template_hub_name", "hub_id", "name"),
        Index("ix_wa_template_hub_status", "hub_id", "meta_status"),
        {"extend_existing": True},
    )

    CATEGORY_CHOICES = ("MARKETING", "UTILITY", "AUTHENTICATION")
    META_STATUS_CHOICES = ("pending", "approved", "rejected")

    name: Mapped[str] = mapped_column(String(255))
    language: Mapped[str] = mapped_column(String(10), default="es", server_default="es")
    category: Mapped[str] = mapped_column(
        String(20), default="UTILITY", server_default="UTILITY",
    )

    # Template body sections
    header: Mapped[str] = mapped_column(Text, default="", server_default="")
    body: Mapped[str] = mapped_column(Text, default="", server_default="")
    footer: Mapped[str] = mapped_column(Text, default="", server_default="")

    # Meta-specific metadata
    meta_template_id: Mapped[str] = mapped_column(
        String(100), default="", server_default="",
    )
    meta_status: Mapped[str] = mapped_column(
        String(20), default="pending", server_default="pending",
    )

    # Variable names referenced in the body, e.g. ["1", "2"] for {{1}}, {{2}}
    variables: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    @property
    def category_label(self) -> str:
        return {
            "MARKETING": "Marketing",
            "UTILITY": "Utility",
            "AUTHENTICATION": "Authentication",
        }.get(self.category, self.category)

    @property
    def meta_status_label(self) -> str:
        return {
            "pending": "Pending approval",
            "approved": "Approved",
            "rejected": "Rejected",
        }.get(self.meta_status, self.meta_status)

    @property
    def meta_status_class(self) -> str:
        return {
            "pending": "warning",
            "approved": "success",
            "rejected": "error",
        }.get(self.meta_status, "neutral")

    def __repr__(self) -> str:
        return f"<WhatsAppTemplate {self.name!r} ({self.meta_status})>"
