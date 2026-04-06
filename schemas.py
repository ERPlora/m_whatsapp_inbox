"""
Pydantic schemas for WhatsApp Inbox module.

Replaces Django forms — used for request validation.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WhatsAppInboxSettingsUpdate(BaseModel):
    """Schema for updating WhatsApp Inbox settings."""

    is_enabled: bool | None = None
    account_mode: str | None = Field(default=None, pattern="^(shared|per_employee)$")
    auto_reply_enabled: bool | None = None
    approval_mode: str | None = Field(default=None, pattern="^(auto|manual)$")
    require_confirmation: bool | None = None
    gpt_system_prompt: str | None = None
    auto_close_hours: int | None = Field(default=None, ge=1, le=168)
    notify_staff_new_request: bool | None = None
    greeting_message: str | None = None
    out_of_hours_message: str | None = None
    input_modules: list[str] | None = None
    output_modules: list[str] | None = None


class InboxRequestNotesUpdate(BaseModel):
    """Schema for updating staff notes on a request."""

    notes: str = ""


class SendMessageCreate(BaseModel):
    """Schema for sending a manual message in a conversation."""

    body: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Lambda webhook schemas
# ---------------------------------------------------------------------------


class IncomingMessagePayload(BaseModel):
    """Single incoming WhatsApp message from Lambda."""

    wa_message_id: str
    direction: str = "inbound"  # inbound | outbound
    message_type: str = "text"
    body: str = ""
    media_url: str = ""
    metadata: dict = Field(default_factory=dict)


class ConversationContext(BaseModel):
    """Conversation identification for incoming message."""

    wa_contact_id: str
    contact_name: str = ""
    contact_phone: str = ""
    phone_number_id: str = ""
    assigned_employee_id: str | None = None


class GPTResult(BaseModel):
    """GPT analysis result from Lambda."""

    response_text: str = ""
    request_type: str | None = None
    parsed_data: dict | None = None
    confidence: float = 0.0


class IncomingWebhookPayload(BaseModel):
    """Full payload from Lambda whatsapp-worker."""

    action: str  # process_message | status_update | send_message
    hub_id: str
    # For process_message
    conversation: ConversationContext | None = None
    message: IncomingMessagePayload | None = None
    gpt_result: GPTResult | None = None
    settings_snapshot: dict = Field(default_factory=dict)  # approval_mode, require_confirmation
    is_new_conversation: bool = False
    # For status_update
    wa_message_id: str | None = None
    status: str | None = None
    # For send_message (manual outbound from Lambda)
    outbound_message: IncomingMessagePayload | None = None
    conversation_id: str | None = None


class StatusUpdatePayload(BaseModel):
    """Status update for a message (delivered, read, failed)."""

    hub_id: str
    wa_message_id: str
    status: str  # sent | delivered | read | failed
