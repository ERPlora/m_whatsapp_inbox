"""
WhatsApp Inbox module services — ModuleService pattern.

Services: ConversationService, RequestService, SettingsService.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

import uuid as _uuid_module

from runtime.models.queryset import HubQuery
from runtime.orm.transactions import atomic
from runtime.apps.service_facade import ModuleService, action

from .bot import DEFAULT_SCHEMAS, INPUT_MODULE_REGISTRY, OUTPUT_MODULE_REGISTRY


# ============================================================================
# Use-case blueprints (shared config for setup/configure)
# ============================================================================

_USE_CASE_BLUEPRINTS = {
    "restaurant": {
        "label": "Restaurant reservations",
        "description": "Use WhatsApp to answer menu questions and capture table reservations.",
        "input_modules": ["inventory", "catalog"],
        "output_modules": ["table_reservations"],
        "request_schema": DEFAULT_SCHEMAS["reservation"],
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
        "output_modules": ["kitchen_orders"],
        "request_schema": DEFAULT_SCHEMAS["order"],
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
        "request_schema": DEFAULT_SCHEMAS["appointment"],
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
        "request_schema": DEFAULT_SCHEMAS["quote"],
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


# ============================================================================
# Module-state helpers (filesystem + registry checks)
# ============================================================================

def _get_modules_dir() -> Path:
    from runtime.config.settings import get_settings
    return Path(get_settings().modules_dir)


def _get_active_module_ids() -> set[str]:
    from runtime.apps.registry import module_registry
    return set(module_registry.active_module_ids())


def _get_module_paths(module_id: str) -> tuple[Path, Path]:
    modules_dir = _get_modules_dir()
    return modules_dir / module_id, modules_dir / f"_{module_id}"


def _get_module_state(module_id: str) -> dict:
    enabled_path, disabled_path = _get_module_paths(module_id)
    active_ids = _get_active_module_ids()
    module_path = None
    if enabled_path.exists():
        module_path = enabled_path
    elif disabled_path.exists():
        module_path = disabled_path

    return {
        "module_id": module_id,
        "runtime_active": module_id in active_ids,
        "enabled_on_disk": enabled_path.exists(),
        "disabled_on_disk": disabled_path.exists(),
        "available_on_disk": enabled_path.exists() or disabled_path.exists(),
        "has_whatsapp_handler": bool(module_path and (module_path / "whatsapp.py").exists()),
    }


def _get_module_label(module_id: str) -> str:
    if module_id in INPUT_MODULE_REGISTRY:
        return INPUT_MODULE_REGISTRY[module_id].get("label", module_id)
    if module_id in OUTPUT_MODULE_REGISTRY:
        return ", ".join(OUTPUT_MODULE_REGISTRY[module_id])
    return module_id


def _serialize_module_option(module_id: str) -> dict:
    state = _get_module_state(module_id)
    supported_as = []
    if module_id in INPUT_MODULE_REGISTRY:
        supported_as.append("input")
    if module_id in OUTPUT_MODULE_REGISTRY:
        supported_as.append("output")
    return {
        "module_id": module_id,
        "label": _get_module_label(module_id),
        "supported_as": supported_as,
        "request_types": OUTPUT_MODULE_REGISTRY.get(module_id, []),
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

    for mid in blueprint["output_modules"]:
        state = _get_module_state(mid)
        if not state["available_on_disk"]:
            missing_modules.append(mid)
            continue
        if not state["has_whatsapp_handler"]:
            unsupported_modules.append(mid)
            continue
        if not state["runtime_active"]:
            activation_required.append(mid)

    for mid in blueprint["input_modules"]:
        state = _get_module_state(mid)
        if state["available_on_disk"]:
            optional_inputs_available.append(mid)
        else:
            optional_inputs_missing.append(mid)

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
    for mid in module_ids:
        state = _get_module_state(mid)
        if not state["enabled_on_disk"]:
            continue
        if require_handler and not state["has_whatsapp_handler"]:
            continue
        selected.append(mid)
        if not state["runtime_active"]:
            pending_activation.append(mid)
    return selected, pending_activation


# ============================================================================
# Conversation Service
# ============================================================================


class ConversationService(ModuleService):
    """WhatsApp conversation management."""

    @action(permission="view_conversation")
    async def list_conversations(
        self,
        *,
        status: str = "",
        search: str = "",
        limit: int = 20,
        user_id: str = "",
    ):
        """List WhatsApp conversations, optionally filtered by status and contact name."""
        from whatsapp_inbox.models import WhatsAppConversation, WhatsAppInboxSettings

        settings = await HubQuery(WhatsAppInboxSettings, self.db, self.hub_id).first()
        q = self.q(WhatsAppConversation).order_by(WhatsAppConversation.last_message_at.desc())

        # Per-employee mode: restrict to assigned conversations
        if settings and settings.account_mode == "per_employee" and user_id:
            q = q.filter(WhatsAppConversation.assigned_to_id == user_id)

        if status:
            q = q.filter(WhatsAppConversation.status == status)
        if search:
            q = q.filter(WhatsAppConversation.contact_name.ilike(f"%{search}%"))

        limit = min(limit, 50)
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

    @action(permission="view_conversation")
    async def get_conversation(
        self,
        *,
        conversation_id: str,
        message_limit: int = 10,
    ):
        """Get conversation details including recent messages and linked requests."""
        from whatsapp_inbox.models import WhatsAppConversation, WhatsAppMessage, InboxRequest

        conv = await self.q(WhatsAppConversation).get(conversation_id)

        limit = min(message_limit, 50)
        messages = await self.q(WhatsAppMessage).filter(
            WhatsAppMessage.conversation_id == conv.id,
        ).order_by(WhatsAppMessage.created_at.desc()).limit(limit).all()

        requests = await self.q(InboxRequest).filter(
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

    @action(permission="manage_settings", mutates=True)
    async def assign_conversation(
        self,
        *,
        conversation_id: str,
        employee_id: str = "",
    ):
        """Reassign a WhatsApp conversation to a different employee."""
        from whatsapp_inbox.models import WhatsAppConversation

        conv = await self.q(WhatsAppConversation).get(conversation_id)
        async with atomic(self.db):
            conv.assigned_to_id = employee_id if employee_id else None
            await self.db.flush()

        return {
            "conversation_id": str(conv.id),
            "assigned_to": str(conv.assigned_to_id) if conv.assigned_to_id else None,
            "status": "ok",
        }


# ============================================================================
# Request Service
# ============================================================================


class RequestService(ModuleService):
    """WhatsApp inbox request management (orders, reservations, appointments)."""

    @action(permission="view_request")
    async def list_requests(
        self,
        *,
        status: str = "",
        request_type: str = "",
        limit: int = 20,
    ):
        """List WhatsApp inbox requests with optional status and type filters."""
        from whatsapp_inbox.models import InboxRequest

        q = self.q(InboxRequest).order_by(InboxRequest.created_at.desc())
        if status:
            q = q.filter(InboxRequest.status == status)
        if request_type:
            q = q.filter(InboxRequest.request_type == request_type)

        limit = min(limit, 50)
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

    @action(permission="view_request")
    async def get_request(self, *, request_id: str):
        """Get full details of a WhatsApp inbox request."""
        from whatsapp_inbox.models import InboxRequest

        r = await self.q(InboxRequest).get(request_id)
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

    @action(permission="change_request", mutates=True)
    async def approve_request(self, *, request_id: str):
        """Approve a pending WhatsApp inbox request."""
        from whatsapp_inbox.models import InboxRequest

        r = await self.q(InboxRequest).get(request_id)
        if r.status != "pending_review":
            return {"error": f"Request is '{r.status}', can only approve 'pending_review'"}

        async with atomic(self.db):
            r.status = "confirmed"
            r.confirmed_at = datetime.now(UTC)
            await self.db.flush()

        return {"status": "approved", "reference_number": r.reference_number}

    @action(permission="change_request", mutates=True)
    async def reject_request(self, *, request_id: str):
        """Reject a pending WhatsApp inbox request."""
        from whatsapp_inbox.models import InboxRequest

        r = await self.q(InboxRequest).get(request_id)
        if r.status != "pending_review":
            return {"error": f"Request is '{r.status}', can only reject 'pending_review'"}

        async with atomic(self.db):
            r.status = "rejected"
            await self.db.flush()

        return {"status": "rejected", "reference_number": r.reference_number}

    @action(permission="change_request", mutates=True)
    async def fulfill_request(
        self,
        *,
        request_id: str,
        create_linked_object: bool = False,
    ):
        """Mark a confirmed request as fulfilled, optionally creating the linked object."""
        from whatsapp_inbox.models import InboxRequest
        from whatsapp_inbox.actions import execute_action

        r = await self.q(InboxRequest).get(request_id)
        if r.status != "confirmed":
            return {"error": f"Request is '{r.status}', can only fulfill 'confirmed'"}

        if create_linked_object:
            action_result = await execute_action(r, self.db)
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

        async with atomic(self.db):
            r.status = "fulfilled"
            r.fulfilled_at = datetime.now(UTC)
            await self.db.flush()

        result = {"status": "fulfilled", "reference_number": r.reference_number}
        if r.linked_module:
            result["linked_module"] = r.linked_module
            result["linked_object_id"] = str(r.linked_object_id)
        return result


# ============================================================================
# Settings Service
# ============================================================================


class SettingsService(ModuleService):
    """WhatsApp Inbox settings and setup configuration."""

    async def _get_settings(self) -> Any:
        from whatsapp_inbox.models import WhatsAppInboxSettings

        obj = await HubQuery(WhatsAppInboxSettings, self.db, self.hub_id).first()
        if obj:
            return obj
        obj = WhatsAppInboxSettings(hub_id=self.hub_id)
        self.db.add(obj)
        await self.db.flush()
        return obj

    @action(permission="manage_settings")
    async def get_settings(self):
        """Get WhatsApp Inbox settings."""
        s = await self._get_settings()
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

    @action(permission="manage_settings", mutates=True)
    async def update_settings(
        self,
        *,
        is_enabled: bool | None = None,
        account_mode: str | None = None,
        auto_reply_enabled: bool | None = None,
        approval_mode: str | None = None,
        require_confirmation: bool | None = None,
        gpt_system_prompt: str | None = None,
        auto_close_hours: int | None = None,
        notify_staff_new_request: bool | None = None,
        greeting_message: str | None = None,
        out_of_hours_message: str | None = None,
        input_modules: list[str] | None = None,
        output_modules: list[str] | None = None,
    ):
        """Update WhatsApp Inbox settings. Only provided fields are updated."""
        s = await self._get_settings()
        updated = []

        fields = {
            "is_enabled": is_enabled,
            "account_mode": account_mode,
            "auto_reply_enabled": auto_reply_enabled,
            "approval_mode": approval_mode,
            "require_confirmation": require_confirmation,
            "gpt_system_prompt": gpt_system_prompt,
            "auto_close_hours": auto_close_hours,
            "notify_staff_new_request": notify_staff_new_request,
            "greeting_message": greeting_message,
            "out_of_hours_message": out_of_hours_message,
            "input_modules": input_modules,
            "output_modules": output_modules,
        }

        async with atomic(self.db):
            for field, value in fields.items():
                if value is not None:
                    setattr(s, field, value)
                    updated.append(field)
            if updated:
                await self.db.flush()

        return {"updated_fields": updated, "status": "ok"}

    @action(permission="manage_settings")
    async def list_setup_options(self):
        """List WhatsApp Inbox setup options: available modules and use-case readiness."""
        relevant_ids = sorted(
            set(INPUT_MODULE_REGISTRY.keys()) | set(OUTPUT_MODULE_REGISTRY.keys())
        )
        return {
            "modules": [_serialize_module_option(mid) for mid in relevant_ids],
            "use_cases": [_assess_use_case(uc) for uc in _USE_CASE_BLUEPRINTS],
            "assistant_can_enable_modules": True,
        }

    @action(permission="manage_settings", mutates=True)
    async def configure(
        self,
        *,
        use_case: str,
        enable_missing_modules: bool = False,
        account_mode: str = "",
        approval_mode: str = "",
        auto_reply_enabled: bool | None = None,
        require_confirmation: bool | None = None,
        replace_system_prompt: bool = False,
        business_info: str = "",
    ):
        """Configure WhatsApp Inbox for a specific business use case."""
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
        if enable_missing_modules:
            for mid in [*blueprint["output_modules"], *blueprint["input_modules"]]:
                state = _get_module_state(mid)
                if state["disabled_on_disk"]:
                    result = _enable_module_on_disk(mid)
                    if result["status"] == "enabled":
                        enabled_modules.append(mid)
                        restart_required = True

        output_mods, pending_output = _select_configured_modules(
            blueprint["output_modules"], require_handler=True,
        )
        if len(output_mods) != len(blueprint["output_modules"]):
            missing_after = sorted(set(blueprint["output_modules"]) - set(output_mods))
            return {
                "status": "missing_modules",
                "use_case": use_case,
                "missing_modules": missing_after,
                "enabled_modules": enabled_modules,
                "error": "Required output modules are still not available for configuration.",
            }

        input_mods, pending_input = _select_configured_modules(
            blueprint["input_modules"], require_handler=False,
        )

        settings_obj = await self._get_settings()
        updates = {
            "is_enabled": True,
            "account_mode": account_mode or blueprint["account_mode"],
            "approval_mode": approval_mode or blueprint["approval_mode"],
            "auto_reply_enabled": (
                auto_reply_enabled if auto_reply_enabled is not None
                else blueprint["auto_reply_enabled"]
            ),
            "require_confirmation": (
                require_confirmation if require_confirmation is not None
                else blueprint["require_confirmation"]
            ),
            "input_modules": input_mods,
            "output_modules": output_mods,
            "request_schema": deepcopy(blueprint["request_schema"]),
        }

        if replace_system_prompt or not settings_obj.gpt_system_prompt:
            prompt_parts = []
            if business_info.strip():
                prompt_parts.append(business_info.strip())
            prompt_parts.append(blueprint["default_prompt"])
            updates["gpt_system_prompt"] = "\n\n".join(prompt_parts)

        updated_fields = []
        async with atomic(self.db):
            for field, value in updates.items():
                if getattr(settings_obj, field) != value:
                    setattr(settings_obj, field, value)
                    updated_fields.append(field)
            if updated_fields:
                await self.db.flush()

        pending_activation = sorted(set(pending_output + pending_input))
        if pending_activation:
            restart_required = True

        return {
            "status": "ok",
            "use_case": use_case,
            "configured_label": blueprint["label"],
            "updated_fields": updated_fields,
            "input_modules": input_mods,
            "output_modules": output_mods,
            "enabled_modules": enabled_modules,
            "pending_activation_modules": pending_activation,
            "restart_required": restart_required,
        }


# ============================================================================
# WhatsApp Template Service
# ============================================================================


class WhatsAppTemplateService(ModuleService):
    """WhatsApp template management — create, list, update, delete, sync with Meta."""

    @action(permission="manage_settings")
    async def list_templates(self, *, active_only: bool = True, limit: int = 50):
        """List WhatsApp templates for this hub."""
        from whatsapp_inbox.models import WhatsAppTemplate

        query = self.q(WhatsAppTemplate)
        if active_only:
            query = query.filter(WhatsAppTemplate.is_active == True)  # noqa: E712

        total = await query.count()
        templates = await query.order_by(WhatsAppTemplate.name.asc()).limit(limit).all()

        return {
            "templates": [{
                "id": str(t.id),
                "name": t.name,
                "language": t.language,
                "category": t.category,
                "category_label": t.category_label,
                "meta_status": t.meta_status,
                "meta_status_label": t.meta_status_label,
                "variables": t.variables,
                "is_active": t.is_active,
                "created_at": str(t.created_at) if t.created_at else None,
            } for t in templates],
            "total": total,
        }

    @action(permission="manage_settings")
    async def get_template(self, *, template_id: str):
        """Get full details of a WhatsApp template."""
        from whatsapp_inbox.models import WhatsAppTemplate

        t = await self.q(WhatsAppTemplate).get(_uuid_module.UUID(template_id))
        if t is None:
            return {"error": "Template not found"}
        return {
            "id": str(t.id),
            "name": t.name,
            "language": t.language,
            "category": t.category,
            "header": t.header,
            "body": t.body,
            "footer": t.footer,
            "meta_template_id": t.meta_template_id,
            "meta_status": t.meta_status,
            "variables": t.variables,
            "is_active": t.is_active,
            "created_at": str(t.created_at) if t.created_at else None,
            "updated_at": str(t.updated_at) if t.updated_at else None,
        }

    @action(permission="manage_settings", mutates=True)
    async def create_template(
        self,
        *,
        name: str,
        language: str = "es",
        category: str = "UTILITY",
        header: str = "",
        body: str = "",
        footer: str = "",
        variables: list[str] | None = None,
    ):
        """Create a new WhatsApp template (starts with meta_status='pending')."""
        from whatsapp_inbox.models import WhatsAppTemplate

        if not name:
            return {"error": "name is required"}
        if category not in ("MARKETING", "UTILITY", "AUTHENTICATION"):
            return {"error": "category must be MARKETING, UTILITY, or AUTHENTICATION"}

        async with atomic(self.db) as session:
            t = WhatsAppTemplate(
                hub_id=self.hub_id,
                name=name,
                language=language,
                category=category,
                header=header,
                body=body,
                footer=footer,
                variables=variables or [],
                meta_status="pending",
                is_active=True,
            )
            session.add(t)
            await session.flush()

        return {
            "id": str(t.id),
            "name": t.name,
            "meta_status": "pending",
            "created": True,
            "note": "Template created locally. Submit to Meta Business Manager for approval.",
        }

    @action(permission="manage_settings", mutates=True)
    async def update_template(
        self,
        *,
        template_id: str,
        name: str | None = None,
        language: str | None = None,
        category: str | None = None,
        header: str | None = None,
        body: str | None = None,
        footer: str | None = None,
        variables: list[str] | None = None,
        is_active: bool | None = None,
    ):
        """Update an existing WhatsApp template. Only provided fields are updated."""
        from whatsapp_inbox.models import WhatsAppTemplate

        t = await self.q(WhatsAppTemplate).get(_uuid_module.UUID(template_id))
        if t is None:
            return {"error": "Template not found"}

        updated = []
        fields = {
            "name": name,
            "language": language,
            "category": category,
            "header": header,
            "body": body,
            "footer": footer,
            "variables": variables,
            "is_active": is_active,
        }
        async with atomic(self.db):
            for field_name, value in fields.items():
                if value is not None:
                    setattr(t, field_name, value)
                    updated.append(field_name)
            if updated:
                # Reset to pending after edit (Meta requires re-approval on content change)
                content_fields = {"name", "language", "category", "header", "body", "footer"}
                if content_fields.intersection(updated):
                    t.meta_status = "pending"
                    if "meta_status" not in updated:
                        updated.append("meta_status")
                await self.db.flush()

        return {
            "id": str(t.id),
            "updated_fields": updated,
            "updated": True,
        }

    @action(permission="manage_settings", mutates=True)
    async def delete_template(self, *, template_id: str):
        """Soft-delete a WhatsApp template."""
        from whatsapp_inbox.models import WhatsAppTemplate

        t = await self.q(WhatsAppTemplate).get(_uuid_module.UUID(template_id))
        if t is None:
            return {"error": "Template not found"}

        async with atomic(self.db):
            t.is_deleted = True
            await self.db.flush()

        return {"id": template_id, "deleted": True}

    @action(permission="manage_settings")
    async def sync_with_meta(self, *, phone_number_id: str = ""):
        """Placeholder: sync templates with Meta Business Manager.

        TODO: Implement Meta Graph API call to GET /{waba_id}/message_templates
        and update local meta_status and meta_template_id for each template.

        Returns a status dict indicating this is a placeholder.
        """
        import logging as _logging
        _logging.getLogger(__name__).info(
            "[WhatsAppTemplateService] sync_with_meta called — not yet implemented",
        )
        return {
            "status": "not_implemented",
            "message": (
                "Meta template sync is not yet implemented. "
                "Update meta_status manually via update_template for now."
            ),
        }
