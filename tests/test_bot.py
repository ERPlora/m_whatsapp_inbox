"""
Tests for whatsapp_inbox bot service.
"""

from __future__ import annotations

from whatsapp_inbox.services.bot import (
    DEFAULT_SCHEMAS,
    get_allowed_request_types,
    validate_request_schema,
)


class TestGetAllowedRequestTypes:
    def test_empty_returns_empty_set(self):
        assert get_allowed_request_types([]) == set()

    def test_orders_module(self):
        assert get_allowed_request_types(["orders"]) == {"order"}

    def test_multiple_modules(self):
        result = get_allowed_request_types(["orders", "reservations"])
        assert result == {"order", "reservation"}

    def test_unknown_module(self):
        assert get_allowed_request_types(["unknown"]) == set()


class TestValidateRequestSchema:
    def test_valid_schema(self):
        is_valid, errors = validate_request_schema(DEFAULT_SCHEMAS["reservation"])
        assert is_valid is True
        assert errors == []

    def test_missing_fields_key(self):
        is_valid, errors = validate_request_schema({})
        assert is_valid is False
        assert "fields" in errors[0]

    def test_not_a_dict(self):
        is_valid, errors = validate_request_schema("not a dict")
        assert is_valid is False

    def test_invalid_field_type(self):
        schema = {
            "fields": [
                {"key": "x", "label": "X", "type": "invalid_type"},
            ],
        }
        is_valid, errors = validate_request_schema(schema)
        assert is_valid is False

    def test_choice_without_choices(self):
        schema = {
            "fields": [
                {"key": "x", "label": "X", "type": "choice"},
            ],
        }
        is_valid, errors = validate_request_schema(schema)
        assert is_valid is False
        assert "choices" in errors[0]
