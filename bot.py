"""WhatsApp bot logic.

Builds GPT prompts, parses responses, and manages the confirmation flow.
The actual GPT calls happen in the Lambda worker — this module provides
helpers for the Hub side (manual message handling, settings validation).
"""

from __future__ import annotations

import json
import logging
from importlib import import_module
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# --- Input Module Registry ---
# Maps module_id -> how to query its data for GPT context
INPUT_MODULE_REGISTRY: dict[str, dict[str, Any]] = {
    "inventory": {
        "model_path": "inventory.models.Product",
        "fields": ["name", "description", "price", "category__name"],
        "filter": {"is_active": True},
        "label": "Products",
    },
    "services": {
        "model_path": "services.models.Service",
        "fields": ["name", "description", "price", "duration_minutes", "category__name"],
        "filter": {"is_active": True},
        "label": "Services",
    },
    "catalog": {
        "model_path": "catalog.models.CatalogItem",
        "fields": ["name", "description", "price", "category__name"],
        "filter": {"is_active": True},
        "label": "Catalog",
    },
}

# --- Output Module Registry ---
# Maps module_id -> which request_types it handles
OUTPUT_MODULE_REGISTRY: dict[str, list[str]] = {
    "orders": ["order"],
    "table_reservations": ["reservation"],
    "appointments": ["appointment"],
    "quotes": ["quote"],
}


async def _query_module_async(config: dict, db: Any, hub_id: UUID) -> list[dict] | None:
    """Import a model dynamically and return formatted rows (async).

    Args:
        config: dict from INPUT_MODULE_REGISTRY
        db: AsyncSession
        hub_id: Hub UUID

    Returns:
        list[dict] or None if module not installed
    """
    from runtime.models.queryset import HubQuery

    try:
        module_path, class_name = config["model_path"].rsplit(".", 1)
        mod = import_module(module_path)
        model_cls = getattr(mod, class_name)
    except (ImportError, AttributeError):
        return None

    q = HubQuery(model_cls, db, hub_id)
    for field_name, value in config.get("filter", {}).items():
        col = getattr(model_cls, field_name, None)
        if col is not None:
            q = q.filter(col == value)

    rows = await q.limit(200).all()
    if not rows:
        return None

    fields = config.get("fields", ["name"])
    result = []
    for row in rows:
        item = {}
        for field in fields:
            if "__" in field:
                # Handle relationship traversal like category__name
                parts = field.split("__")
                val = row
                for part in parts:
                    val = getattr(val, part, None) if val else None
                item[field.replace("__", "_")] = val
            else:
                item[field] = getattr(row, field, None)
        result.append(item)
    return result


async def build_catalog_context_async(input_modules: list[str], db: Any, hub_id: UUID) -> str:
    """Query input module tables and format as text for GPT prompt (async).

    Args:
        input_modules: list of module IDs
        db: AsyncSession
        hub_id: Hub UUID

    Returns:
        str: formatted catalog text, or empty string
    """
    if not input_modules:
        return ""

    sections = []
    for module_id in input_modules:
        config = INPUT_MODULE_REGISTRY.get(module_id)
        if not config:
            continue

        rows = await _query_module_async(config, db, hub_id)
        if not rows:
            continue

        label = config.get("label", module_id)
        lines = [f"\n## {label}"]
        for row in rows:
            parts = []
            for k, v in row.items():
                if v is not None:
                    key = k.replace("__name", "")
                    parts.append(f"{key}: {v}")
            lines.append("- " + ", ".join(parts))
        sections.append("\n".join(lines))

    if not sections:
        return ""
    return "\nAVAILABLE CATALOG:\n" + "\n".join(sections) + "\n"


def build_catalog_context(input_modules: list[str]) -> str:
    """Sync version — query input module tables and format as text for GPT prompt.

    Used by Lambda workers and sync contexts.

    Args:
        input_modules: list of module IDs

    Returns:
        str: formatted catalog text, or empty string
    """
    if not input_modules:
        return ""

    sections = []
    for module_id in input_modules:
        config = INPUT_MODULE_REGISTRY.get(module_id)
        if not config:
            continue

        rows = _query_module_sync(config)
        if not rows:
            continue

        label = config.get("label", module_id)
        lines = [f"\n## {label}"]
        for row in rows:
            parts = []
            for k, v in row.items():
                if v is not None:
                    key = k.replace("__name", "")
                    parts.append(f"{key}: {v}")
            lines.append("- " + ", ".join(parts))
        sections.append("\n".join(lines))

    if not sections:
        return ""
    return "\nAVAILABLE CATALOG:\n" + "\n".join(sections) + "\n"


def _query_module_sync(config: dict) -> list[dict] | None:
    """Sync model query — fallback for Lambda / non-async contexts."""
    try:
        module_path, class_name = config["model_path"].rsplit(".", 1)
        mod = import_module(module_path)
        getattr(mod, class_name)
    except (ImportError, AttributeError):
        return None
    # In Hub Next, sync queries are not directly available.
    # This is a stub for compatibility — async version is preferred.
    return None


def get_allowed_request_types(output_modules: list[str]) -> set[str]:
    """Return the set of request_types allowed by the configured output modules.

    Args:
        output_modules: list of module IDs

    Returns:
        set[str]: allowed request type strings
    """
    if not output_modules:
        return set()

    allowed = set()
    for module_id in output_modules:
        types = OUTPUT_MODULE_REGISTRY.get(module_id, [])
        allowed.update(types)
    return allowed


def build_output_context(output_modules: list[str], hub_id: UUID) -> str:
    """Query output modules for additional GPT context (availability, staff, etc.).

    Each output module can expose a ``whatsapp.py`` with ``get_context_for_bot(hub_id)``.

    Args:
        output_modules: list of module IDs
        hub_id: Hub UUID

    Returns:
        str: formatted context text, or empty string
    """
    if not output_modules or not hub_id:
        return ""

    sections = []
    for module_id in output_modules:
        try:
            whatsapp_mod = import_module(f"{module_id}.whatsapp")
            if hasattr(whatsapp_mod, "get_context_for_bot"):
                context = whatsapp_mod.get_context_for_bot(hub_id)
                if context:
                    sections.append(context)
        except ImportError:
            pass

    return "".join(sections)


def build_system_prompt(settings: Any, hub_id: UUID | None = None) -> str:
    """Build the GPT system prompt from WhatsAppInboxSettings.

    Args:
        settings: WhatsAppInboxSettings instance
        hub_id: Hub UUID (for output module context)

    Returns:
        str: Full system prompt for GPT
    """
    if not hub_id:
        hub_id = getattr(settings, "hub_id", None)

    base = """You are a WhatsApp assistant for a business. Your job is to:
1. Understand customer messages and respond helpfully
2. Detect when a customer is making a request (order, reservation, appointment, etc.)
3. Extract structured data from requests

Always respond in the same language as the customer.

IMPORTANT: Always respond with a JSON object with these fields:
- "response_text": your response message to the customer (string)
- "request_type": if this is a request, the type (order/reservation/appointment/quote/transport/custom), otherwise null
- "parsed_data": if this is a request, structured data extracted from the message (object), otherwise null
- "confidence": if this is a request, your confidence in the parsing (0.0-1.0), otherwise 0.0

If asking for confirmation, include a summary in response_text.
"""

    # Constrain request types to configured output modules
    output_modules = getattr(settings, "output_modules", None) or []
    allowed = get_allowed_request_types(output_modules)
    if allowed:
        types_str = ", ".join(sorted(allowed))
        base += f"\nOnly use these request_types: {types_str}\n"

    if settings.gpt_system_prompt:
        base += f"\n\nBusiness information:\n{settings.gpt_system_prompt}\n"

    if settings.request_schema:
        schema_str = json.dumps(settings.request_schema, ensure_ascii=False, indent=2)
        base += f"\n\nRequest schema (fields to extract):\n{schema_str}\n"

    # Inject catalog from input modules
    catalog = build_catalog_context(getattr(settings, "input_modules", None) or [])
    if catalog:
        base += catalog
        base += "\nUse the catalog above to match customer requests to real products/services. "
        base += "Include exact names and prices in parsed_data when possible.\n"

    # Inject context from output modules
    output_context = build_output_context(output_modules, hub_id)
    if output_context:
        base += output_context
        base += "\nUse the information above to check availability before confirming. "
        base += "If the customer asks for an appointment, always ask for service and preferred date/time.\n"

    return base


def validate_request_schema(schema: dict) -> tuple[bool, list[str]]:
    """Validate a request_schema JSON structure.

    Args:
        schema: dict — the request schema to validate

    Returns:
        tuple: (is_valid: bool, errors: list[str])
    """
    errors = []

    if not isinstance(schema, dict):
        return False, ["Schema must be a JSON object"]

    if "fields" not in schema:
        return False, ["Schema must have a 'fields' key"]

    fields = schema.get("fields", [])
    if not isinstance(fields, list):
        return False, ["'fields' must be an array"]

    valid_types = {"text", "number", "choice", "date", "datetime", "line_items", "boolean"}

    for i, field in enumerate(fields):
        if not isinstance(field, dict):
            errors.append(f"Field {i} must be an object")
            continue

        if "key" not in field:
            errors.append(f"Field {i} is missing 'key'")
        if "label" not in field:
            errors.append(f"Field {i} is missing 'label'")

        field_type = field.get("type", "text")
        if field_type not in valid_types:
            errors.append(f"Field {i} has invalid type '{field_type}'. Valid: {valid_types}")

        if field_type == "choice" and not field.get("choices"):
            errors.append(f"Field {i} is 'choice' type but has no 'choices' list")

    return len(errors) == 0, errors


# Default schemas per business request type
DEFAULT_SCHEMAS: dict[str, dict] = {
    "order": {
        "request_type": "order",
        "label": "Pedido",
        "fields": [
            {"key": "items", "type": "line_items", "label": "Productos", "required": True},
            {"key": "order_type", "type": "choice", "label": "Tipo", "choices": ["delivery", "pickup"]},
            {"key": "delivery_address", "type": "text", "label": "Delivery address"},
            {"key": "requested_time", "type": "datetime", "label": "Requested time"},
            {"key": "notes", "type": "text", "label": "Notes"},
        ],
    },
    "reservation": {
        "request_type": "reservation",
        "label": "Reserva",
        "fields": [
            {"key": "party_size", "type": "number", "label": "Party size", "required": True},
            {"key": "date", "type": "date", "label": "Date", "required": True},
            {"key": "time", "type": "datetime", "label": "Time", "required": True},
            {"key": "notes", "type": "text", "label": "Notes"},
        ],
    },
    "appointment": {
        "request_type": "appointment",
        "label": "Cita",
        "fields": [
            {"key": "service", "type": "text", "label": "Service", "required": True},
            {"key": "date", "type": "date", "label": "Date", "required": True},
            {"key": "time", "type": "datetime", "label": "Time", "required": True},
            {"key": "staff_preference", "type": "text", "label": "Preferred staff member"},
            {"key": "notes", "type": "text", "label": "Notes"},
        ],
    },
    "quote": {
        "request_type": "quote",
        "label": "Presupuesto",
        "fields": [
            {"key": "description", "type": "text", "label": "Description", "required": True},
            {"key": "quantity", "type": "number", "label": "Quantity"},
            {"key": "notes", "type": "text", "label": "Notes"},
        ],
    },
    "transport": {
        "request_type": "transport",
        "label": "Transporte",
        "fields": [
            {"key": "origin", "type": "text", "label": "Origin", "required": True},
            {"key": "destination", "type": "text", "label": "Destination", "required": True},
            {"key": "cargo_type", "type": "choice", "label": "Cargo type",
             "choices": ["parcel", "pallet", "bulk", "refrigerated"]},
            {"key": "weight_kg", "type": "number", "label": "Weight (kg)"},
            {"key": "pickup_date", "type": "date", "label": "Pickup date", "required": True},
            {"key": "notes", "type": "text", "label": "Notes"},
        ],
    },
}
