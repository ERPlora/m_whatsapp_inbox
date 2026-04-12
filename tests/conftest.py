"""
Test fixtures for whatsapp_inbox module.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def whatsapp_settings_data():
    """Default WhatsApp Inbox settings data."""
    return {
        "is_enabled": True,
        "account_mode": "shared",
        "auto_reply_enabled": True,
        "approval_mode": "manual",
        "require_confirmation": True,
        "gpt_system_prompt": "You are a restaurant assistant.",
        "auto_close_hours": 24,
        "notify_staff_new_request": True,
        "greeting_message": "Welcome!",
        "out_of_hours_message": "We are closed.",
        "input_modules": ["inventory"],
        "output_modules": ["table_reservations"],
        "request_schema": {
            "fields": [
                {"key": "party_size", "type": "number", "label": "Party size", "required": True},
                {"key": "date", "type": "date", "label": "Date", "required": True},
            ],
        },
    }


@pytest.fixture
def conversation_data():
    """Default conversation data."""
    return {
        "wa_contact_id": "34612345678",
        "contact_name": "Test Customer",
        "contact_phone": "+34 612 345 678",
        "status": "active",
        "unread_count": 0,
    }


@pytest.fixture
def inbox_request_data():
    """Default inbox request data."""
    return {
        "reference_number": "REQ-20260404-0001",
        "request_type": "reservation",
        "status": "pending_review",
        "data": {"party_size": 4, "date": "2026-04-10"},
        "raw_summary": "Table for 4 on April 10",
        "confidence_score": 0.92,
    }
