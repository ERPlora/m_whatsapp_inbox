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
