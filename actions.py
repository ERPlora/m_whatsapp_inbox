"""Action handlers — convert confirmed InboxRequests into real module objects.

Uses a decoupled pattern: each output module exposes a ``whatsapp.py`` file
with a standard interface (check_availability, create_from_request,
get_context_for_bot). This module dispatches generically without importing
target module code directly.
"""

from __future__ import annotations

import logging
from importlib import import_module
from typing import TYPE_CHECKING

from .bot import OUTPUT_MODULE_REGISTRY, get_allowed_request_types

if TYPE_CHECKING:
    from whatsapp_inbox.models import InboxRequest

logger = logging.getLogger(__name__)

# Maps request_type -> module_id (reverse of OUTPUT_MODULE_REGISTRY)
_REQUEST_TYPE_TO_MODULE: dict[str, str] = {}
for _mod_id, _types in OUTPUT_MODULE_REGISTRY.items():
    for _rt in _types:
        _REQUEST_TYPE_TO_MODULE[_rt] = _mod_id


def has_action_handler(request_type: str) -> bool:
    """Return whether the request type maps to an output module."""
    return request_type in _REQUEST_TYPE_TO_MODULE


async def execute_action(inbox_request: InboxRequest, db: object = None) -> str | bool:
    """Execute the action for a confirmed InboxRequest.

    Dispatches to the target module's whatsapp.py using dynamic import.
    Checks availability first, then creates the object.

    Args:
        inbox_request: InboxRequest instance (status must be 'confirmed')
        db: AsyncSession (optional, for flushing changes)

    Returns:
        True if action was executed successfully
        'unavailable' if no availability
        'skipped' if no action applies for this request
        'failed' if an action should have run but did not complete
    """
    module_id = _REQUEST_TYPE_TO_MODULE.get(inbox_request.request_type)
    if not module_id:
        logger.info(
            "No output module for request_type=%s (ref=%s)",
            inbox_request.request_type, inbox_request.reference_number,
        )
        return "skipped"

    # Validate against output_modules configuration
    settings = await _get_settings(inbox_request, db)
    if settings and settings.output_modules:
        allowed = get_allowed_request_types(settings.output_modules)
        if allowed and inbox_request.request_type not in allowed:
            logger.info(
                "request_type=%s not in output_modules (allowed=%s, ref=%s)",
                inbox_request.request_type, allowed, inbox_request.reference_number,
            )
            return "skipped"

    # Import the target module's whatsapp.py
    try:
        whatsapp_mod = import_module(f"{module_id}.whatsapp")
    except ImportError:
        logger.info(
            "Module %s has no whatsapp.py (ref=%s)",
            module_id, inbox_request.reference_number,
        )
        return "skipped"

    data = inbox_request.data or {}
    hub_id = inbox_request.hub_id

    # Check availability if the module supports it
    if hasattr(whatsapp_mod, "check_availability"):
        try:
            availability = whatsapp_mod.check_availability(hub_id, data)
            if not availability.get("available", True):
                data["_alternatives"] = availability.get("alternatives", [])
                data["_unavailable_reason"] = availability.get("details", {}).get("reason", "")
                inbox_request.data = data
                if db:
                    await db.flush()
                logger.info(
                    "No availability for %s (ref=%s, reason=%s)",
                    module_id, inbox_request.reference_number,
                    availability.get("details", {}).get("reason", "unknown"),
                )
                return "unavailable"
        except Exception:
            logger.exception("Availability check failed for %s", inbox_request.reference_number)

    # Create the object
    try:
        result = whatsapp_mod.create_from_request(
            hub_id,
            data,
            customer=None,  # Would need to load customer relationship
            conversation=inbox_request.conversation,
        )
        if result:
            inbox_request.linked_module = result["module"]
            inbox_request.linked_object_id = result["object_id"]
            if db:
                await db.flush()
            logger.info(
                "Created %s %s from InboxRequest %s",
                result["module"], result["object_id"], inbox_request.reference_number,
            )
            return True
    except Exception:
        logger.exception("Failed to create object for %s", inbox_request.reference_number)

    return "failed"


async def _get_settings(inbox_request: InboxRequest, db: object = None) -> object | None:
    """Load WhatsAppInboxSettings for the request's hub."""
    try:
        from app.core.db.query import HubQuery
        from whatsapp_inbox.models import WhatsAppInboxSettings

        if db:
            return await HubQuery(WhatsAppInboxSettings, db, inbox_request.hub_id).first()
    except Exception:
        pass
    return None
