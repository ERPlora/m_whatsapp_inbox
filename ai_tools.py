"""WhatsApp Inbox AI tools for the Hub Assistant.

Tools for managing WhatsApp conversations, requests, and settings
via the AI assistant chat interface.

All tools are async and use HubQuery for DB access.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any


from app.ai.registry import AssistantTool, register_tool
from app.core.db.query import HubQuery
from app.core.db.transactions import atomic

from .services import bot
from datetime import UTC


_USE_CASE_BLUEPRINTS = {
    "restaurant": {
        "label": "Restaurant reservations",
        "description": "Use WhatsApp to answer menu questions and capture table reservations.",
        "input_modules": ["inventory", "catalog"],
        "output_modules": ["reservations"],
        "request_schema": bot.DEFAULT_SCHEMAS["reservation"],
        "approval_mode": "manual",
        "account_mode": "shared",
        "require_confirmation": True,
        "auto_reply_enabled": True,
        "default_prompt": (
            "You manage a restaurant over WhatsApp. Help customers with menu questions and "
            "collect reservation details clearly: party size, date, time, and notes."
        ),
    },
    "sales": {
        "label": "Sales and orders",
        "description": "Use WhatsApp to answer product questions and capture customer orders.",
        "input_modules": ["inventory", "catalog"],
        "output_modules": ["commands"],
        "request_schema": bot.DEFAULT_SCHEMAS["order"],
        "approval_mode": "manual",
        "account_mode": "shared",
        "require_confirmation": True,
        "auto_reply_enabled": True,
        "default_prompt": (
            "You manage product sales over WhatsApp. Use the product catalog when answering "
            "questions and extract order items, quantities, delivery or pickup preference, "
            "requested time, and notes."
        ),
    },
    "appointments": {
        "label": "Appointments and bookings",
        "description": "Use WhatsApp to book services such as salon, beauty, or clinic appointments.",
        "input_modules": ["services"],
        "output_modules": ["appointments"],
        "request_schema": bot.DEFAULT_SCHEMAS["appointment"],
        "approval_mode": "manual",
        "account_mode": "shared",
        "require_confirmation": True,
        "auto_reply_enabled": True,
        "default_prompt": (
            "You manage appointments over WhatsApp. Ask for the service, preferred date and time, "
            "and any relevant notes before confirming."
        ),
    },
    "quotes": {
        "label": "Quotes and estimates",
        "description": "Use WhatsApp to capture quote requests before staff review.",
        "input_modules": ["catalog", "services"],
        "output_modules": ["quotes"],
        "request_schema": bot.DEFAULT_SCHEMAS["quote"],
        "approval_mode": "manual",
        "account_mode": "shared",
        "require_confirmation": True,
        "auto_reply_enabled": True,
        "default_prompt": (
            "You manage quote requests over WhatsApp. Clarify what the customer needs, capture a "
            "clean description, quantities when relevant, and any notes for the team."
        ),
    },
}


def _get_modules_dir() -> Path:
    from app.config.settings import get_settings
    config = get_settings()
    return Path(config.modules_dir)


def _get_active_module_ids() -> set[str]:
    from app.modules.registry import module_registry
    return set(module_registry.active_module_ids())


def _get_module_paths(module_id: str) -> tuple[Path, Path]:
    modules_dir = _get_modules_dir()
    return modules_dir / module_id, modules_dir / f"_{module_id}"


def _get_module_state(module_id: str) -> dict:
    enabled_path, disabled_path = _get_module_paths(module_id)
    active_module_ids = _get_active_module_ids()
    module_path = None
    if enabled_path.exists():
        module_path = enabled_path
    elif disabled_path.exists():
        module_path = disabled_path

    return {
        "module_id": module_id,
        "runtime_active": module_id in active_module_ids,
        "enabled_on_disk": enabled_path.exists(),
        "disabled_on_disk": disabled_path.exists(),
        "available_on_disk": enabled_path.exists() or disabled_path.exists(),
        "has_whatsapp_handler": bool(module_path and (module_path / "whatsapp.py").exists()),
    }


def _get_module_label(module_id: str) -> str:
    if module_id in bot.INPUT_MODULE_REGISTRY:
        return bot.INPUT_MODULE_REGISTRY[module_id].get("label", module_id)
    if module_id in bot.OUTPUT_MODULE_REGISTRY:
        return ", ".join(bot.OUTPUT_MODULE_REGISTRY[module_id])
    return module_id


def _serialize_module_option(module_id: str) -> dict:
    state = _get_module_state(module_id)
    supported_as = []
    if module_id in bot.INPUT_MODULE_REGISTRY:
        supported_as.append("input")
    if module_id in bot.OUTPUT_MODULE_REGISTRY:
        supported_as.append("output")

    return {
        "module_id": module_id,
        "label": _get_module_label(module_id),
        "supported_as": supported_as,
        "request_types": bot.OUTPUT_MODULE_REGISTRY.get(module_id, []),
        "runtime_active": state["runtime_active"],
        "enabled_on_disk": state["enabled_on_disk"],
        "disabled_on_disk": state["disabled_on_disk"],
        "has_whatsapp_handler": state["has_whatsapp_handler"],
    }


def _assess_use_case(use_case: str) -> dict:
    blueprint = _USE_CASE_BLUEPRINTS[use_case]

    activation_required = []
    missing_modules = []
    unsupported_modules = []
    optional_inputs_available = []
    optional_inputs_missing = []

    for module_id in blueprint["output_modules"]:
        state = _get_module_state(module_id)
        if not state["available_on_disk"]:
            missing_modules.append(module_id)
            continue
        if not state["has_whatsapp_handler"]:
            unsupported_modules.append(module_id)
            continue
        if not state["runtime_active"]:
            activation_required.append(module_id)

    for module_id in blueprint["input_modules"]:
        state = _get_module_state(module_id)
        if state["available_on_disk"]:
            optional_inputs_available.append(module_id)
        else:
            optional_inputs_missing.append(module_id)

    status = "ready"
    if unsupported_modules:
        status = "unsupported"
    elif missing_modules:
        status = "missing_modules"
    elif activation_required:
        status = "requires_activation"

    return {
        "use_case": use_case,
        "label": blueprint["label"],
        "description": blueprint["description"],
        "status": status,
        "recommended_input_modules": blueprint["input_modules"],
        "recommended_output_modules": blueprint["output_modules"],
        "available_input_modules": optional_inputs_available,
        "missing_input_modules": optional_inputs_missing,
        "activation_required_modules": activation_required,
        "missing_modules": missing_modules,
        "unsupported_modules": unsupported_modules,
    }


def _enable_module_on_disk(module_id: str) -> dict:
    enabled_path, disabled_path = _get_module_paths(module_id)
    if enabled_path.exists():
        return {"module_id": module_id, "status": "already_enabled"}
    if not disabled_path.exists():
        return {"module_id": module_id, "status": "missing"}

    disabled_path.rename(enabled_path)
    return {"module_id": module_id, "status": "enabled"}


def _select_configured_modules(
    module_ids: list[str], require_handler: bool = False,
) -> tuple[list[str], list[str]]:
    selected = []
    pending_activation = []
    for module_id in module_ids:
        state = _get_module_state(module_id)
        if not state["enabled_on_disk"]:
            continue
        if require_handler and not state["has_whatsapp_handler"]:
            continue
        selected.append(module_id)
        if not state["runtime_active"]:
            pending_activation.append(module_id)
    return selected, pending_activation


async def _get_settings_for_hub(hub_id, db) -> Any:
    from .models import WhatsAppInboxSettings

    settings_obj = await HubQuery(WhatsAppInboxSettings, db, hub_id).first()
    if settings_obj:
        return settings_obj
    settings_obj = WhatsAppInboxSettings(hub_id=hub_id)
    db.add(settings_obj)
    await db.flush()
    return settings_obj


# ==============================================================================
# CONVERSATIONS
# ==============================================================================

@register_tool
class ListConversations(AssistantTool):
    name = "list_whatsapp_conversations"
    description = (
        "List WhatsApp conversations. "
        "Filter by status (active, waiting_confirmation, closed). "
        "Returns contact name, phone, status, unread count, and last message time."
    )
    module_id = "whatsapp_inbox"
    required_permission = "whatsapp_inbox.view_conversation"
    parameters = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["active", "waiting_confirmation", "closed"],
                "description": "Filter by conversation status",
            },
            "search": {
                "type": "string",
                "description": "Search by contact name",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 20)",
            },
        },
        "required": [],
        "additionalProperties": False,
    }

    async def execute(self, args: dict, request: Any) -> dict:
        from .models import WhatsAppConversation, WhatsAppInboxSettings

        db = request.state.db
        hub_id = request.state.hub_id

        settings = await HubQuery(WhatsAppInboxSettings, db, hub_id).first()
        q = HubQuery(WhatsAppConversation, db, hub_id)
        q = q.order_by(WhatsAppConversation.last_message_at.desc())

        # Per-employee mode: non-admin employees see only their conversations
        if settings and settings.account_mode == "per_employee":
            local_user = getattr(request.state, "user", None)
            role = getattr(local_user, "role_obj", None) if local_user else None
            if not (role and role.name == "admin"):
                q = q.filter(WhatsAppConversation.assigned_to_id == local_user.id)

        if args.get("status"):
            q = q.filter(WhatsAppConversation.status == args["status"])
        if args.get("search"):
            q = q.filter(WhatsAppConversation.contact_name.ilike(f"%{args['search']}%"))

        limit = min(args.get("limit", 20), 50)
        conversations = await q.limit(limit).all()
        total = await q.count()

        return {
            "conversations": [
                {
                    "id": str(c.id),
                    "contact_name": c.contact_name,
                    "contact_phone": c.contact_phone,
                    "status": c.status,
                    "unread_count": c.unread_count,
                    "assigned_to": str(c.assigned_to_id) if c.assigned_to_id else None,
                    "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
                }
                for c in conversations
            ],
            "total": total,
        }


@register_tool
class GetConversation(AssistantTool):
    name = "get_whatsapp_conversation"
    description = (
        "Get details of a WhatsApp conversation including recent messages. "
        "Returns conversation info, last N messages, and linked requests."
    )
    module_id = "whatsapp_inbox"
    required_permission = "whatsapp_inbox.view_conversation"
    parameters = {
        "type": "object",
        "properties": {
            "conversation_id": {
                "type": "string",
                "description": "Conversation UUID",
            },
            "message_limit": {
                "type": "integer",
                "description": "Number of recent messages to include (default 10)",
            },
        },
        "required": ["conversation_id"],
        "additionalProperties": False,
    }

    async def execute(self, args: dict, request: Any) -> dict:
        from .models import WhatsAppConversation, WhatsAppMessage, InboxRequest

        db = request.state.db
        hub_id = request.state.hub_id

        conv = await HubQuery(WhatsAppConversation, db, hub_id).get(args["conversation_id"])

        limit = min(args.get("message_limit", 10), 50)
        messages = await HubQuery(WhatsAppMessage, db, hub_id).filter(
            WhatsAppMessage.conversation_id == conv.id,
        ).order_by(WhatsAppMessage.created_at.desc()).limit(limit).all()

        requests = await HubQuery(InboxRequest, db, hub_id).filter(
            InboxRequest.conversation_id == conv.id,
        ).order_by(InboxRequest.created_at.desc()).limit(5).all()

        return {
            "conversation": {
                "id": str(conv.id),
                "contact_name": conv.contact_name,
                "contact_phone": conv.contact_phone,
                "status": conv.status,
                "unread_count": conv.unread_count,
                "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
            },
            "messages": [
                {
                    "direction": m.direction,
                    "body": m.body,
                    "message_type": m.message_type,
                    "status": m.status,
                    "created_at": m.created_at.isoformat(),
                }
                for m in reversed(list(messages))
            ],
            "requests": [
                {
                    "id": str(r.id),
                    "reference_number": r.reference_number,
                    "request_type": r.request_type,
                    "status": r.status,
                }
                for r in requests
            ],
        }


# ==============================================================================
# REQUESTS
# ==============================================================================

@register_tool
class ListInboxRequests(AssistantTool):
    name = "list_whatsapp_requests"
    description = (
        "List WhatsApp inbox requests (orders, reservations, appointments parsed from messages). "
        "Filter by status (pending_review, confirmed, rejected, fulfilled, cancelled) and type."
    )
    module_id = "whatsapp_inbox"
    required_permission = "whatsapp_inbox.view_request"
    parameters = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["pending_review", "confirmed", "rejected", "fulfilled", "cancelled"],
                "description": "Filter by request status",
            },
            "request_type": {
                "type": "string",
                "enum": ["order", "reservation", "appointment", "quote", "transport", "custom"],
                "description": "Filter by request type",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (default 20)",
            },
        },
        "required": [],
        "additionalProperties": False,
    }

    async def execute(self, args: dict, request: Any) -> dict:
        from .models import InboxRequest

        db = request.state.db
        hub_id = request.state.hub_id

        q = HubQuery(InboxRequest, db, hub_id).order_by(InboxRequest.created_at.desc())

        if args.get("status"):
            q = q.filter(InboxRequest.status == args["status"])
        if args.get("request_type"):
            q = q.filter(InboxRequest.request_type == args["request_type"])

        limit = min(args.get("limit", 20), 50)
        requests_list = await q.limit(limit).all()
        total = await q.count()

        return {
            "requests": [
                {
                    "id": str(r.id),
                    "reference_number": r.reference_number,
                    "request_type": r.request_type,
                    "status": r.status,
                    "contact_name": r.conversation.contact_name if r.conversation else "",
                    "raw_summary": r.raw_summary[:200] if r.raw_summary else "",
                    "confidence": r.confidence_percent,
                    "created_at": r.created_at.isoformat(),
                }
                for r in requests_list
            ],
            "total": total,
        }


@register_tool
class GetInboxRequest(AssistantTool):
    name = "get_whatsapp_request"
    description = (
        "Get full details of a WhatsApp inbox request including parsed data, "
        "summary, confidence, linked objects, and staff notes."
    )
    module_id = "whatsapp_inbox"
    required_permission = "whatsapp_inbox.view_request"
    parameters = {
        "type": "object",
        "properties": {
            "request_id": {
                "type": "string",
                "description": "InboxRequest UUID",
            },
        },
        "required": ["request_id"],
        "additionalProperties": False,
    }

    async def execute(self, args: dict, request: Any) -> dict:
        from .models import InboxRequest

        db = request.state.db
        hub_id = request.state.hub_id

        r = await HubQuery(InboxRequest, db, hub_id).get(args["request_id"])

        return {
            "request": {
                "id": str(r.id),
                "reference_number": r.reference_number,
                "request_type": r.request_type,
                "status": r.status,
                "data": r.data,
                "raw_summary": r.raw_summary,
                "confidence": r.confidence_percent,
                "notes": r.notes,
                "contact_name": r.conversation.contact_name if r.conversation else "",
                "customer": str(r.customer_id) if r.customer_id else None,
                "assigned_to": str(r.assigned_to_id) if r.assigned_to_id else None,
                "linked_module": r.linked_module,
                "linked_object_id": str(r.linked_object_id) if r.linked_object_id else None,
                "confirmed_at": r.confirmed_at.isoformat() if r.confirmed_at else None,
                "fulfilled_at": r.fulfilled_at.isoformat() if r.fulfilled_at else None,
                "created_at": r.created_at.isoformat(),
            },
        }


@register_tool
class ApproveInboxRequest(AssistantTool):
    name = "approve_whatsapp_request"
    description = "Approve a pending WhatsApp inbox request. Changes status to confirmed."
    module_id = "whatsapp_inbox"
    required_permission = "whatsapp_inbox.change_request"
    requires_confirmation = True
    parameters = {
        "type": "object",
        "properties": {
            "request_id": {
                "type": "string",
                "description": "InboxRequest UUID to approve",
            },
        },
        "required": ["request_id"],
        "additionalProperties": False,
    }

    def get_confirmation_data(self, args: dict, request: Any) -> dict:
        # Confirmation data is built synchronously from cache or minimal data
        return {
            "title": f"Approve request {args['request_id'][:8]}...",
            "rows": [{"label": "Action", "value": "Approve"}],
            "badge": "color-success",
        }

    async def execute(self, args: dict, request: Any) -> dict:
        from datetime import datetime
        from .models import InboxRequest

        db = request.state.db
        hub_id = request.state.hub_id

        r = await HubQuery(InboxRequest, db, hub_id).get(args["request_id"])
        if r.status != "pending_review":
            return {"error": f"Request is '{r.status}', can only approve 'pending_review'"}

        async with atomic(db):
            r.status = "confirmed"
            r.confirmed_at = datetime.now(UTC)
            await db.flush()

        return {"status": "approved", "reference_number": r.reference_number}


@register_tool
class RejectInboxRequest(AssistantTool):
    name = "reject_whatsapp_request"
    description = "Reject a pending WhatsApp inbox request."
    module_id = "whatsapp_inbox"
    required_permission = "whatsapp_inbox.change_request"
    requires_confirmation = True
    parameters = {
        "type": "object",
        "properties": {
            "request_id": {
                "type": "string",
                "description": "InboxRequest UUID to reject",
            },
        },
        "required": ["request_id"],
        "additionalProperties": False,
    }

    async def execute(self, args: dict, request: Any) -> dict:
        from .models import InboxRequest

        db = request.state.db
        hub_id = request.state.hub_id

        r = await HubQuery(InboxRequest, db, hub_id).get(args["request_id"])
        if r.status != "pending_review":
            return {"error": f"Request is '{r.status}', can only reject 'pending_review'"}

        async with atomic(db):
            r.status = "rejected"
            await db.flush()

        return {"status": "rejected", "reference_number": r.reference_number}


@register_tool
class FulfillInboxRequest(AssistantTool):
    name = "fulfill_whatsapp_request"
    description = "Mark a confirmed request as fulfilled. Optionally creates the linked object in the target module."
    module_id = "whatsapp_inbox"
    required_permission = "whatsapp_inbox.change_request"
    requires_confirmation = True
    parameters = {
        "type": "object",
        "properties": {
            "request_id": {
                "type": "string",
                "description": "InboxRequest UUID to fulfill",
            },
            "create_linked_object": {
                "type": "boolean",
                "description": "Whether to create the linked object in the target module",
            },
        },
        "required": ["request_id"],
        "additionalProperties": False,
    }

    async def execute(self, args: dict, request: Any) -> dict:
        from datetime import datetime
        from .models import InboxRequest
        from .services.actions import execute_action

        db = request.state.db
        hub_id = request.state.hub_id

        r = await HubQuery(InboxRequest, db, hub_id).get(args["request_id"])
        if r.status != "confirmed":
            return {"error": f"Request is '{r.status}', can only fulfill 'confirmed'"}

        if args.get("create_linked_object", False):
            action_result = await execute_action(r, db)
            if action_result == "unavailable":
                return {
                    "error": "The linked request is not available right now.",
                    "reference_number": r.reference_number,
                }
            if action_result == "failed":
                return {
                    "error": "The linked record could not be created.",
                    "reference_number": r.reference_number,
                }

        async with atomic(db):
            r.status = "fulfilled"
            r.fulfilled_at = datetime.now(UTC)
            await db.flush()

        result = {"status": "fulfilled", "reference_number": r.reference_number}
        if r.linked_module:
            result["linked_module"] = r.linked_module
            result["linked_object_id"] = str(r.linked_object_id)
        return result


# ==============================================================================
# SETTINGS
# ==============================================================================

@register_tool
class GetWhatsAppSettings(AssistantTool):
    name = "get_whatsapp_inbox_settings"
    description = (
        "Get WhatsApp Inbox settings: enabled status, account mode, auto-reply, approval mode, "
        "GPT prompt, greeting/out-of-hours messages, input/output modules, and request schema."
    )
    module_id = "whatsapp_inbox"
    required_permission = "whatsapp_inbox.manage_settings"
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    async def execute(self, args: dict, request: Any) -> dict:
        db = request.state.db
        hub_id = request.state.hub_id

        s = await _get_settings_for_hub(hub_id, db)
        return {
            "settings": {
                "is_enabled": s.is_enabled,
                "account_mode": s.account_mode,
                "auto_reply_enabled": s.auto_reply_enabled,
                "approval_mode": s.approval_mode,
                "require_confirmation": s.require_confirmation,
                "gpt_system_prompt": s.gpt_system_prompt[:500] if s.gpt_system_prompt else "",
                "auto_close_hours": s.auto_close_hours,
                "notify_staff_new_request": s.notify_staff_new_request,
                "greeting_message": s.greeting_message,
                "out_of_hours_message": s.out_of_hours_message,
                "input_modules": s.input_modules,
                "output_modules": s.output_modules,
                "request_schema": s.request_schema,
            },
        }


@register_tool
class UpdateWhatsAppSettings(AssistantTool):
    name = "update_whatsapp_inbox_settings"
    description = (
        "Update WhatsApp Inbox settings. Only provided fields are updated. "
        "Supports: is_enabled, account_mode, auto_reply_enabled, approval_mode, "
        "require_confirmation, gpt_system_prompt, auto_close_hours, "
        "notify_staff_new_request, greeting_message, out_of_hours_message, "
        "input_modules, output_modules."
    )
    module_id = "whatsapp_inbox"
    required_permission = "whatsapp_inbox.manage_settings"
    requires_confirmation = True
    parameters = {
        "type": "object",
        "properties": {
            "is_enabled": {"type": "boolean", "description": "Enable/disable the module"},
            "account_mode": {
                "type": "string",
                "enum": ["shared", "per_employee"],
                "description": "shared: one number for all. per_employee: each salesperson has their own.",
            },
            "auto_reply_enabled": {"type": "boolean", "description": "Enable/disable GPT auto-reply"},
            "approval_mode": {
                "type": "string",
                "enum": ["auto", "manual"],
                "description": "auto: bot handles requests. manual: staff must approve.",
            },
            "require_confirmation": {
                "type": "boolean",
                "description": "Auto mode only: ask client to confirm before creating request",
            },
            "gpt_system_prompt": {
                "type": "string",
                "description": "Custom GPT instructions (business info, hours, menu, FAQs)",
            },
            "auto_close_hours": {
                "type": "integer",
                "description": "Close conversations after N hours of inactivity",
            },
            "notify_staff_new_request": {
                "type": "boolean",
                "description": "Notify staff when a new request is created",
            },
            "greeting_message": {
                "type": "string",
                "description": "Welcome message for new conversations",
            },
            "out_of_hours_message": {
                "type": "string",
                "description": "Message sent outside business hours",
            },
            "input_modules": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Module IDs for GPT context (e.g. ['inventory', 'services'])",
            },
            "output_modules": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Module IDs for output (e.g. ['orders', 'reservations'])",
            },
        },
        "required": [],
        "additionalProperties": False,
    }

    async def execute(self, args: dict, request: Any) -> dict:
        db = request.state.db
        hub_id = request.state.hub_id

        s = await _get_settings_for_hub(hub_id, db)
        updated = []

        fields = [
            "is_enabled", "account_mode", "auto_reply_enabled", "approval_mode",
            "require_confirmation", "gpt_system_prompt", "auto_close_hours",
            "notify_staff_new_request", "greeting_message", "out_of_hours_message",
            "input_modules", "output_modules",
        ]

        async with atomic(db):
            for field in fields:
                if field in args:
                    setattr(s, field, args[field])
                    updated.append(field)
            if updated:
                await db.flush()

        return {"updated_fields": updated, "status": "ok"}


# ==============================================================================
# SETUP
# ==============================================================================

@register_tool
class ListWhatsAppSetupOptions(AssistantTool):
    name = "list_whatsapp_setup_options"
    description = (
        "List WhatsApp Inbox setup options for this hub. "
        "Returns installed or disabled modules relevant to WhatsApp and the readiness of common use cases "
        "such as restaurant reservations, sales orders, and appointments."
    )
    module_id = "whatsapp_inbox"
    required_permission = "whatsapp_inbox.manage_settings"
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    async def execute(self, args: dict, request: Any) -> dict:
        relevant_module_ids = sorted(
            set(bot.INPUT_MODULE_REGISTRY.keys()) | set(bot.OUTPUT_MODULE_REGISTRY.keys())
        )
        return {
            "modules": [_serialize_module_option(module_id) for module_id in relevant_module_ids],
            "use_cases": [_assess_use_case(use_case) for use_case in _USE_CASE_BLUEPRINTS],
            "assistant_can_enable_modules": True,
        }


@register_tool
class ConfigureWhatsAppInbox(AssistantTool):
    name = "configure_whatsapp_inbox"
    description = (
        "Configure WhatsApp Inbox for a specific business use case. "
        "The assistant can pick the recommended input and output modules, apply the matching request schema, "
        "enable the module, and optionally activate disabled dependencies that exist on disk."
    )
    module_id = "whatsapp_inbox"
    required_permission = "whatsapp_inbox.manage_settings"
    requires_confirmation = True
    parameters = {
        "type": "object",
        "properties": {
            "use_case": {
                "type": "string",
                "enum": sorted(_USE_CASE_BLUEPRINTS.keys()),
                "description": "Target business flow: restaurant, sales, appointments, or quotes.",
            },
            "enable_missing_modules": {
                "type": "boolean",
                "description": "If true, enable relevant disabled modules found on disk.",
            },
            "account_mode": {
                "type": "string",
                "enum": ["shared", "per_employee"],
                "description": "Optional override for account mode.",
            },
            "approval_mode": {
                "type": "string",
                "enum": ["auto", "manual"],
                "description": "Optional override for approval mode.",
            },
            "auto_reply_enabled": {
                "type": "boolean",
                "description": "Optional override for GPT auto-reply.",
            },
            "require_confirmation": {
                "type": "boolean",
                "description": "Optional override for customer confirmation before creating requests.",
            },
            "replace_system_prompt": {
                "type": "boolean",
                "description": "Replace the current GPT system prompt instead of only filling it when empty.",
            },
            "business_info": {
                "type": "string",
                "description": "Business-specific prompt content to prepend to the generated default prompt.",
            },
        },
        "required": ["use_case"],
        "additionalProperties": False,
    }

    async def execute(self, args: dict, request: Any) -> dict:
        db = request.state.db
        hub_id = request.state.hub_id

        use_case = args["use_case"]
        blueprint = _USE_CASE_BLUEPRINTS[use_case]
        assessment = _assess_use_case(use_case)

        if assessment["unsupported_modules"]:
            return {
                "status": "unsupported",
                "use_case": use_case,
                "unsupported_modules": assessment["unsupported_modules"],
                "error": "One or more required modules do not expose a WhatsApp handler yet.",
            }

        if assessment["missing_modules"]:
            return {
                "status": "missing_modules",
                "use_case": use_case,
                "missing_modules": assessment["missing_modules"],
                "error": "One or more required modules are not present on disk.",
            }

        enabled_modules = []
        restart_required = False
        if args.get("enable_missing_modules", False):
            for module_id in [*blueprint["output_modules"], *blueprint["input_modules"]]:
                state = _get_module_state(module_id)
                if state["disabled_on_disk"]:
                    result = _enable_module_on_disk(module_id)
                    if result["status"] == "enabled":
                        enabled_modules.append(module_id)
                        restart_required = True

        output_modules, pending_output_activation = _select_configured_modules(
            blueprint["output_modules"],
            require_handler=True,
        )
        if len(output_modules) != len(blueprint["output_modules"]):
            missing_after_enable = sorted(set(blueprint["output_modules"]) - set(output_modules))
            return {
                "status": "missing_modules",
                "use_case": use_case,
                "missing_modules": missing_after_enable,
                "enabled_modules": enabled_modules,
                "error": "Required output modules are still not available for configuration.",
            }

        input_modules, pending_input_activation = _select_configured_modules(
            blueprint["input_modules"],
            require_handler=False,
        )

        settings_obj = await _get_settings_for_hub(hub_id, db)
        updates = {
            "is_enabled": True,
            "account_mode": args.get("account_mode", blueprint["account_mode"]),
            "approval_mode": args.get("approval_mode", blueprint["approval_mode"]),
            "auto_reply_enabled": args.get("auto_reply_enabled", blueprint["auto_reply_enabled"]),
            "require_confirmation": args.get("require_confirmation", blueprint["require_confirmation"]),
            "input_modules": input_modules,
            "output_modules": output_modules,
            "request_schema": deepcopy(blueprint["request_schema"]),
        }

        replace_prompt = args.get("replace_system_prompt", False)
        business_info = args.get("business_info", "").strip()
        if replace_prompt or not settings_obj.gpt_system_prompt:
            prompt_parts = []
            if business_info:
                prompt_parts.append(business_info)
            prompt_parts.append(blueprint["default_prompt"])
            updates["gpt_system_prompt"] = "\n\n".join(prompt_parts)

        updated_fields = []
        async with atomic(db):
            for field, value in updates.items():
                if getattr(settings_obj, field) != value:
                    setattr(settings_obj, field, value)
                    updated_fields.append(field)
            if updated_fields:
                await db.flush()

        pending_activation = sorted(set(pending_output_activation + pending_input_activation))
        if pending_activation:
            restart_required = True

        return {
            "status": "ok",
            "use_case": use_case,
            "configured_label": blueprint["label"],
            "updated_fields": updated_fields,
            "input_modules": input_modules,
            "output_modules": output_modules,
            "enabled_modules": enabled_modules,
            "pending_activation_modules": pending_activation,
            "restart_required": restart_required,
        }


# ==============================================================================
# MULTI-ACCOUNT
# ==============================================================================

@register_tool
class AssignConversation(AssistantTool):
    name = "assign_whatsapp_conversation"
    description = (
        "Reassign a WhatsApp conversation to a different employee. "
        "Used in per-employee mode to transfer conversations between team members."
    )
    module_id = "whatsapp_inbox"
    required_permission = "whatsapp_inbox.manage_settings"
    requires_confirmation = True
    parameters = {
        "type": "object",
        "properties": {
            "conversation_id": {
                "type": "string",
                "description": "Conversation UUID to reassign",
            },
            "employee_id": {
                "type": "string",
                "description": "LocalUser UUID of the employee to assign to (or empty to unassign)",
            },
        },
        "required": ["conversation_id"],
        "additionalProperties": False,
    }

    async def execute(self, args: dict, request: Any) -> dict:
        from .models import WhatsAppConversation

        db = request.state.db
        hub_id = request.state.hub_id

        conv = await HubQuery(WhatsAppConversation, db, hub_id).get(args["conversation_id"])

        employee_id = args.get("employee_id")
        async with atomic(db):
            conv.assigned_to_id = employee_id if employee_id else None
            await db.flush()

        return {
            "conversation_id": str(conv.id),
            "assigned_to": str(conv.assigned_to_id) if conv.assigned_to_id else None,
            "status": "ok",
        }
