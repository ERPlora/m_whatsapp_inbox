"""
Tests for whatsapp_inbox models.
"""

from __future__ import annotations

from whatsapp_inbox.models import InboxRequest


class TestInboxRequest:
    """Tests for InboxRequest model properties."""

    def test_confidence_percent(self):
        req = InboxRequest.__new__(InboxRequest)
        req.confidence_score = 0.92
        assert req.confidence_percent == 92

    def test_confidence_percent_zero(self):
        req = InboxRequest.__new__(InboxRequest)
        req.confidence_score = 0.0
        assert req.confidence_percent == 0

    def test_status_class(self):
        req = InboxRequest.__new__(InboxRequest)
        req.status = "pending_review"
        assert req.status_class == "warning"

        req.status = "confirmed"
        assert req.status_class == "primary"

        req.status = "fulfilled"
        assert req.status_class == "success"

        req.status = "rejected"
        assert req.status_class == "error"

    def test_data_pretty(self):
        req = InboxRequest.__new__(InboxRequest)
        req.data = {"party_size": 4}
        assert '"party_size": 4' in req.data_pretty

    def test_data_pretty_empty(self):
        req = InboxRequest.__new__(InboxRequest)
        req.data = {}
        assert req.data_pretty == "{}"

    def test_request_type_display(self):
        req = InboxRequest.__new__(InboxRequest)
        req.request_type = "reservation"
        assert req.request_type_display == "Reservation"

    def test_status_display(self):
        req = InboxRequest.__new__(InboxRequest)
        req.status = "pending_review"
        assert req.status_display == "Pending Review"
