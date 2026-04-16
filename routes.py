"""
WhatsApp Inbox HTMX views — FastAPI router.

Replaces Django views.py + urls.py. Uses @htmx_view decorator
(partial for HTMX requests, full page for direct navigation).
Mounted at /m/whatsapp_inbox/ by ModuleRuntime.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, UTC

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.db.query import HubQuery
from app.core.db.transactions import atomic
from app.core.dependencies import CurrentUser, DbSession, HubId
from app.core.htmx import add_message, htmx_redirect, htmx_view

from .models import (
    EmployeeWhatsAppLink,
    InboxRequest,
    WhatsAppConversation,
    WhatsAppInboxSettings,
    WhatsAppMessage,
    WhatsAppTemplate,
)
from . import bot

logger = logging.getLogger(__name__)

router = APIRouter()

_FULFILL_ERROR_MESSAGES = {
    "failed": "The linked record could not be created. The request remains confirmed.",
    "unavailable": "The request is not currently available. Review alternatives before fulfilling it.",
}


# ==============================================================================
# Helpers
# ==============================================================================

def _q(model, db, hub_id):
    return HubQuery(model, db, hub_id)


def _is_admin(user) -> bool:
    """Check if user has admin role."""
    if not user:
        return False
    get_role_name = getattr(user, "get_role_name", None)
    if callable(get_role_name):
        return get_role_name() == "admin"
    role = getattr(user, "role_obj", None)
    return bool(role and role.name == "admin")


def _is_employee_scoped(settings: WhatsAppInboxSettings) -> bool:
    return settings.account_mode == "per_employee"


def _can_access_assignment(user, settings: WhatsAppInboxSettings, assigned_to_id) -> bool:
    if not _is_employee_scoped(settings) or _is_admin(user):
        return True
    return bool(user and assigned_to_id and assigned_to_id == user.id)


async def _get_settings(db, hub_id) -> WhatsAppInboxSettings:
    """Get or create WhatsAppInboxSettings for hub."""
    settings = await _q(WhatsAppInboxSettings, db, hub_id).first()
    if settings:
        return settings
    settings = WhatsAppInboxSettings(hub_id=hub_id)
    db.add(settings)
    await db.flush()
    return settings


async def _get_cloud_helpers() -> tuple[str, str]:
    """Get Cloud API URL and auth token."""
    from app.config.settings import get_settings
    config = get_settings()
    cloud_url = config.cloud_api_url or "https://erplora.com"
    auth_token = config.hub_jwt or ""
    return cloud_url, auth_token


async def _get_plan_info() -> dict | None:
    """Fetch WhatsApp plan and usage from Cloud API."""
    try:
        cloud_url, auth_token = await _get_cloud_helpers()
        if not auth_token:
            return None
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{cloud_url}/api/v1/hub/device/whatsapp/plan/",
                headers={"X-Hub-Token": auth_token},
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        logger.exception("Failed to fetch WhatsApp plan info")
    return None


async def _get_connected_numbers() -> list[dict]:
    """Fetch connected WhatsApp numbers from Cloud API."""
    try:
        cloud_url, auth_token = await _get_cloud_helpers()
        if not auth_token:
            return []
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{cloud_url}/api/v1/hub/device/whatsapp/numbers/",
                headers={"X-Hub-Token": auth_token},
            )
            if resp.status_code == 200:
                return resp.json().get("numbers", [])
    except Exception:
        logger.exception("Failed to fetch WhatsApp numbers from Cloud")
    return []


def _build_setup_notice(settings: WhatsAppInboxSettings, connected_numbers=None) -> dict | None:
    missing_items = []
    if not settings.is_enabled:
        missing_items.append("Enable the WhatsApp Inbox module.")
    if not settings.gpt_system_prompt.strip():
        missing_items.append("Add business instructions so the assistant knows how to answer customers.")
    schema_fields = (settings.request_schema or {}).get("fields", [])
    if not schema_fields:
        missing_items.append("Define the request schema the bot should extract from messages.")
    if not settings.output_modules:
        missing_items.append("Select at least one output module for orders, reservations, appointments, or quotes.")
    if connected_numbers is not None and not connected_numbers:
        missing_items.append("Connect at least one WhatsApp number before going live.")

    if not missing_items:
        return None

    return {
        "title": "Configuration required before using WhatsApp Inbox",
        "message": "Configure this module with the AI assistant or manually in Settings before using it with real customers.",
        # Keep the key as 'missing_items' — 'items' collides with dict.items in Jinja
        "missing_items": missing_items,
    }


# ==============================================================================
# INBOX (Conversations)
# ==============================================================================

@router.get("/")
@router.get("/inbox")
@htmx_view(module_id="whatsapp_inbox", view_id="inbox", partial_template="whatsapp_inbox/partials/inbox_list.html")
async def inbox(request: Request, db: DbSession, user: CurrentUser, hub_id: HubId):
    settings = await _get_settings(db, hub_id)
    query = _q(WhatsAppConversation, db, hub_id)

    # Per-employee scoping
    if _is_employee_scoped(settings) and not _is_admin(user):
        query = query.filter(WhatsAppConversation.assigned_to_id == user.id)

    # Employee filter (for admins in per-employee mode)
    employee_filter = request.query_params.get("employee", "")
    if employee_filter and _is_employee_scoped(settings):
        query = query.filter(WhatsAppConversation.assigned_to_id == employee_filter)

    search = request.query_params.get("q", "").strip()
    if search:
        query = query.filter(WhatsAppConversation.contact_name.ilike(f"%{search}%"))

    status_filter = request.query_params.get("status", "")
    if status_filter:
        query = query.filter(WhatsAppConversation.status == status_filter)

    conversations = await query.order_by(WhatsAppConversation.last_message_at.desc()).all()

    # Employees list for filter dropdown (admin only, per-employee mode)
    employees = []
    if _is_employee_scoped(settings) and _is_admin(user):
        employees = await _q(EmployeeWhatsAppLink, db, hub_id).filter(
            EmployeeWhatsAppLink.is_active == True,  # noqa: E712
        ).all()

    return {
        "conversations": conversations,
        "search": search,
        "status_filter": status_filter,
        "settings": settings,
        "employees": employees,
        "employee_filter": employee_filter,
        "setup_notice": _build_setup_notice(settings),
    }


@router.get("/conversation/{pk}")
@htmx_view(module_id="whatsapp_inbox", view_id="inbox", partial_template="whatsapp_inbox/partials/inbox_list.html")
async def conversation_detail(
    request: Request, pk: str, db: DbSession, user: CurrentUser, hub_id: HubId,
):
    settings = await _get_settings(db, hub_id)
    conversation = await _q(WhatsAppConversation, db, hub_id).get(pk)
    if not conversation:
        return htmx_redirect("/m/whatsapp_inbox/")

    if not _can_access_assignment(user, settings, conversation.assigned_to_id):
        add_message(request, "error", "You don't have permission to access this conversation.")
        return htmx_redirect("/m/whatsapp_inbox/")

    messages = await _q(WhatsAppMessage, db, hub_id).filter(
        WhatsAppMessage.conversation_id == conversation.id,
    ).order_by(WhatsAppMessage.created_at).all()

    # Mark as read
    if conversation.unread_count > 0:
        conversation.unread_count = 0
        await db.flush()

    # Linked requests
    inbox_requests = await _q(InboxRequest, db, hub_id).filter(
        InboxRequest.conversation_id == conversation.id,
    ).order_by(InboxRequest.created_at.desc()).all()

    return {
        "conversation": conversation,
        "messages": messages,
        "inbox_requests": inbox_requests,
    }


@router.post("/conversation/{pk}/send")
async def conversation_send(
    request: Request, pk: str, db: DbSession, user: CurrentUser, hub_id: HubId,
):
    """Send a manual message in a conversation (staff -> client via Meta API)."""
    settings = await _get_settings(db, hub_id)
    conversation = await _q(WhatsAppConversation, db, hub_id).get(pk)
    if not conversation:
        return htmx_redirect("/m/whatsapp_inbox/")

    if not _can_access_assignment(user, settings, conversation.assigned_to_id):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    form = await request.form()
    body = form.get("body", "").strip()

    if body:
        async with atomic(db):
            message = WhatsAppMessage(
                hub_id=hub_id,
                conversation_id=conversation.id,
                direction="outbound",
                wa_message_id=f"manual_{uuid.uuid4().hex[:16]}",
                message_type="text",
                body=body,
                status="sent",
            )
            db.add(message)

            conversation.last_message_at = datetime.now(UTC)
            await db.flush()

        # Dispatch send task to Cloud
        phone_number_id = conversation.phone_number_id
        if phone_number_id:
            try:
                cloud_url, auth_token = await _get_cloud_helpers()
                if auth_token:
                    async with httpx.AsyncClient(timeout=10) as client:
                        await client.post(
                            f"{cloud_url}/api/v1/hub/device/tasks/",
                            json={
                                "task": "whatsapp.send_message",
                                "payload": {
                                    "hub_id": str(hub_id),
                                    "phone_number_id": phone_number_id,
                                    "to_number": conversation.wa_contact_id,
                                    "text": body,
                                    "wa_message_id": str(message.id),
                                },
                            },
                            headers={"X-Hub-Token": auth_token, "Content-Type": "application/json"},
                        )
            except Exception:
                logger.exception("Failed to dispatch whatsapp.send_message task")

    return htmx_redirect(f"/m/whatsapp_inbox/conversation/{pk}")


@router.post("/conversation/{pk}/close")
async def conversation_close(
    request: Request, pk: str, db: DbSession, user: CurrentUser, hub_id: HubId,
):
    """Close a conversation."""
    settings = await _get_settings(db, hub_id)
    conversation = await _q(WhatsAppConversation, db, hub_id).get(pk)
    if not conversation:
        return htmx_redirect("/m/whatsapp_inbox/")

    if not _can_access_assignment(user, settings, conversation.assigned_to_id):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    async with atomic(db):
        conversation.status = "closed"
        await db.flush()

    return htmx_redirect("/m/whatsapp_inbox/")


# ==============================================================================
# REQUESTS
# ==============================================================================

@router.get("/requests")
@htmx_view(module_id="whatsapp_inbox", view_id="requests")
async def requests_list(request: Request, db: DbSession, user: CurrentUser, hub_id: HubId):
    settings = await _get_settings(db, hub_id)
    query = _q(InboxRequest, db, hub_id)

    # Per-employee scoping
    if _is_employee_scoped(settings) and not _is_admin(user):
        query = query.filter(InboxRequest.conversation.has(
            WhatsAppConversation.assigned_to_id == user.id,
        ))

    status_filter = request.query_params.get("status", "")
    if status_filter:
        query = query.filter(InboxRequest.status == status_filter)

    type_filter = request.query_params.get("type", "")
    if type_filter:
        query = query.filter(InboxRequest.request_type == type_filter)

    search = request.query_params.get("q", "").strip()
    if search:
        query = query.filter(InboxRequest.reference_number.ilike(f"%{search}%"))

    inbox_requests = await query.order_by(InboxRequest.created_at.desc()).all()

    # Counts for filter badges
    base_q = _q(InboxRequest, db, hub_id)
    if _is_employee_scoped(settings) and not _is_admin(user):
        base_q = base_q.filter(InboxRequest.conversation.has(
            WhatsAppConversation.assigned_to_id == user.id,
        ))

    all_count = await base_q.count()
    pending_count = await base_q.filter(InboxRequest.status == "pending_review").count()
    confirmed_count = await base_q.filter(InboxRequest.status == "confirmed").count()
    fulfilled_count = await base_q.filter(InboxRequest.status == "fulfilled").count()

    return {
        "inbox_requests": inbox_requests,
        "status_filter": status_filter,
        "type_filter": type_filter,
        "search": search,
        "all_count": all_count,
        "pending_count": pending_count,
        "confirmed_count": confirmed_count,
        "fulfilled_count": fulfilled_count,
        "setup_notice": _build_setup_notice(settings),
    }


@router.get("/requests/{pk}")
@htmx_view(module_id="whatsapp_inbox", view_id="requests")
async def request_detail(
    request: Request, pk: str, db: DbSession, user: CurrentUser, hub_id: HubId,
):
    settings = await _get_settings(db, hub_id)
    inbox_request = await _q(InboxRequest, db, hub_id).get(pk)
    if not inbox_request:
        return htmx_redirect("/m/whatsapp_inbox/requests")

    # Access check
    conv = inbox_request.conversation
    assigned_to_id = conv.assigned_to_id if conv else None
    if not _can_access_assignment(user, settings, assigned_to_id):
        add_message(request, "error", "You don't have permission to access this request.")
        return htmx_redirect("/m/whatsapp_inbox/requests")

    # Build display data from schema + request data
    schema = settings.request_schema or {}
    schema_fields = schema.get("fields", [])
    display_fields = []
    for field_def in schema_fields:
        key = field_def.get("key", "")
        value = inbox_request.data.get(key)
        if value is not None:
            display_fields.append({
                "label": field_def.get("label", key),
                "value": value,
                "type": field_def.get("type", "text"),
            })

    fulfill_error_code = request.query_params.get("fulfill_error", "")
    fulfill_error = _FULFILL_ERROR_MESSAGES.get(fulfill_error_code, "")

    return {
        "inbox_request": inbox_request,
        "display_fields": display_fields,
        "fulfill_error": fulfill_error,
    }


@router.post("/requests/{pk}/approve")
async def request_approve(
    request: Request, pk: str, db: DbSession, user: CurrentUser, hub_id: HubId,
):
    settings = await _get_settings(db, hub_id)
    inbox_request = await _q(InboxRequest, db, hub_id).get(pk)
    if not inbox_request:
        return htmx_redirect("/m/whatsapp_inbox/requests")

    conv = inbox_request.conversation
    if not _can_access_assignment(user, settings, conv.assigned_to_id if conv else None):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    if inbox_request.status == "pending_review":
        async with atomic(db):
            inbox_request.status = "confirmed"
            inbox_request.confirmed_at = datetime.now(UTC)
            await db.flush()

    return htmx_redirect(f"/m/whatsapp_inbox/requests/{pk}")


@router.post("/requests/{pk}/reject")
async def request_reject(
    request: Request, pk: str, db: DbSession, user: CurrentUser, hub_id: HubId,
):
    settings = await _get_settings(db, hub_id)
    inbox_request = await _q(InboxRequest, db, hub_id).get(pk)
    if not inbox_request:
        return htmx_redirect("/m/whatsapp_inbox/requests")

    conv = inbox_request.conversation
    if not _can_access_assignment(user, settings, conv.assigned_to_id if conv else None):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    if inbox_request.status == "pending_review":
        async with atomic(db):
            inbox_request.status = "rejected"
            await db.flush()

    return htmx_redirect(f"/m/whatsapp_inbox/requests/{pk}")


@router.post("/requests/{pk}/fulfill")
async def request_fulfill(
    request: Request, pk: str, db: DbSession, user: CurrentUser, hub_id: HubId,
):
    settings = await _get_settings(db, hub_id)
    inbox_request = await _q(InboxRequest, db, hub_id).get(pk)
    if not inbox_request:
        return htmx_redirect("/m/whatsapp_inbox/requests")

    conv = inbox_request.conversation
    if not _can_access_assignment(user, settings, conv.assigned_to_id if conv else None):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    if inbox_request.status == "confirmed":
        from .actions import execute_action, has_action_handler

        if not inbox_request.linked_module and has_action_handler(inbox_request.request_type):
            action_result = await execute_action(inbox_request, db)
            if action_result == "unavailable":
                return htmx_redirect(f"/m/whatsapp_inbox/requests/{pk}?fulfill_error=unavailable")
            if action_result == "failed":
                return htmx_redirect(f"/m/whatsapp_inbox/requests/{pk}?fulfill_error=failed")

        async with atomic(db):
            inbox_request.status = "fulfilled"
            inbox_request.fulfilled_at = datetime.now(UTC)
            await db.flush()

    return htmx_redirect(f"/m/whatsapp_inbox/requests/{pk}")


@router.post("/requests/{pk}/notes")
async def request_save_notes(
    request: Request, pk: str, db: DbSession, user: CurrentUser, hub_id: HubId,
):
    """Save staff notes on a request."""
    settings = await _get_settings(db, hub_id)
    inbox_request = await _q(InboxRequest, db, hub_id).get(pk)
    if not inbox_request:
        return htmx_redirect("/m/whatsapp_inbox/requests")

    conv = inbox_request.conversation
    if not _can_access_assignment(user, settings, conv.assigned_to_id if conv else None):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    form = await request.form()
    notes = form.get("notes", "")

    async with atomic(db):
        inbox_request.notes = notes
        await db.flush()

    return htmx_redirect(f"/m/whatsapp_inbox/requests/{pk}")


# ==============================================================================
# SETTINGS
# ==============================================================================

@router.get("/settings")
@htmx_view(module_id="whatsapp_inbox", view_id="settings")
async def settings_view(request: Request, db: DbSession, user: CurrentUser, hub_id: HubId):
    settings = await _get_settings(db, hub_id)

    # Stats

    conv_total = await _q(WhatsAppConversation, db, hub_id).count()
    conv_active = await _q(WhatsAppConversation, db, hub_id).filter(
        WhatsAppConversation.status == "active",
    ).count()
    conv_waiting = await _q(WhatsAppConversation, db, hub_id).filter(
        WhatsAppConversation.status == "waiting_confirmation",
    ).count()

    req_total = await _q(InboxRequest, db, hub_id).count()
    req_pending = await _q(InboxRequest, db, hub_id).filter(
        InboxRequest.status == "pending_review",
    ).count()
    req_confirmed = await _q(InboxRequest, db, hub_id).filter(
        InboxRequest.status == "confirmed",
    ).count()
    req_fulfilled = await _q(InboxRequest, db, hub_id).filter(
        InboxRequest.status == "fulfilled",
    ).count()

    conversation_stats = {
        "total": conv_total,
        "active": conv_active,
        "waiting": conv_waiting,
    }
    request_stats = {
        "total": req_total,
        "pending": req_pending,
        "confirmed": req_confirmed,
        "fulfilled": req_fulfilled,
    }

    # Input/output module candidates (only show installed modules)
    from app.modules.registry import module_registry
    installed = set(module_registry.active_module_ids())
    input_candidates = [
        {"id": mid, "label": cfg["label"]}
        for mid, cfg in bot.INPUT_MODULE_REGISTRY.items()
        if mid in installed
    ]
    output_candidates = [
        {"id": mid, "label": ", ".join(types)}
        for mid, types in bot.OUTPUT_MODULE_REGISTRY.items()
        if mid in installed
    ]

    # Connected WhatsApp numbers (from Cloud API)
    connected_numbers = await _get_connected_numbers()

    # Employee links (for per-employee mode)
    employee_links = await _q(EmployeeWhatsAppLink, db, hub_id).all()

    # Plan & usage info from Cloud
    plan_info = await _get_plan_info()

    return {
        "settings": settings,
        "conversation_stats": conversation_stats,
        "request_stats": request_stats,
        "input_candidates": input_candidates,
        "output_candidates": output_candidates,
        "connected_numbers": connected_numbers,
        "employee_links": employee_links,
        "plan_info": plan_info or {},
        "setup_notice": _build_setup_notice(settings, connected_numbers),
    }


@router.post("/settings")
async def settings_save(request: Request, db: DbSession, user: CurrentUser, hub_id: HubId):
    """Save WhatsApp Inbox settings."""
    settings = await _get_settings(db, hub_id)
    form = await request.form()

    async with atomic(db):
        settings.is_enabled = "is_enabled" in form
        settings.account_mode = form.get("account_mode", "shared")
        settings.auto_reply_enabled = "auto_reply_enabled" in form
        settings.approval_mode = form.get("approval_mode", "auto")
        settings.require_confirmation = "require_confirmation" in form
        settings.gpt_system_prompt = form.get("gpt_system_prompt", "")
        settings.auto_close_hours = int(form.get("auto_close_hours", "24"))
        settings.notify_staff_new_request = "notify_staff_new_request" in form
        settings.greeting_message = form.get("greeting_message", "")
        settings.out_of_hours_message = form.get("out_of_hours_message", "")
        settings.input_modules = form.getlist("input_modules")
        settings.output_modules = form.getlist("output_modules")
        await db.flush()

    add_message(request, "success", "Settings saved")
    return htmx_redirect("/m/whatsapp_inbox/settings")


# ==============================================================================
# WHATSAPP CONNECT / DISCONNECT
# ==============================================================================

@router.post("/settings/connect")
async def whatsapp_connect(request: Request, db: DbSession, user: CurrentUser, hub_id: HubId):
    """Receive Meta Embedded Signup OAuth code from frontend JS,
    forward to Cloud API to exchange for access token."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    code = data.get("code")
    employee_id = data.get("employee_id", "")

    if not code:
        return JSONResponse({"error": "Missing code"}, status_code=400)

    try:
        cloud_url, auth_token = await _get_cloud_helpers()
        if not auth_token:
            return JSONResponse({"error": "Hub not connected to Cloud"}, status_code=400)

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{cloud_url}/api/v1/hub/device/whatsapp/connect/",
                json={"code": code, "employee_id": employee_id},
                headers={"X-Hub-Token": auth_token, "Content-Type": "application/json"},
            )
            result = resp.json()

        if resp.status_code == 200 and result.get("phone_number_id"):
            phone_number_id = result["phone_number_id"]
            display_phone = result.get("display_phone", "")

            # If per-employee mode and employee_id provided, create local link
            if employee_id:
                async with atomic(db):
                    existing = await _q(EmployeeWhatsAppLink, db, hub_id).filter(
                        EmployeeWhatsAppLink.employee_id == employee_id,
                    ).first()
                    if existing:
                        existing.phone_number_id = phone_number_id
                        existing.display_phone = display_phone
                        existing.is_active = True
                    else:
                        link = EmployeeWhatsAppLink(
                            hub_id=hub_id,
                            employee_id=employee_id,
                            phone_number_id=phone_number_id,
                            display_phone=display_phone,
                            is_active=True,
                        )
                        db.add(link)
                    await db.flush()

            return JSONResponse({
                "success": True,
                "phone_number_id": phone_number_id,
                "display_phone": display_phone,
            })

        return JSONResponse({"error": result.get("error", "Unknown error")}, status_code=502)

    except Exception:
        logger.exception("Failed to connect WhatsApp")
        return JSONResponse({"error": "Connection failed"}, status_code=500)


@router.post("/settings/disconnect/{phone_number_id}")
async def whatsapp_disconnect(
    request: Request, phone_number_id: str, db: DbSession, user: CurrentUser, hub_id: HubId,
):
    """Disconnect a WhatsApp number."""
    try:
        cloud_url, auth_token = await _get_cloud_helpers()
        if auth_token:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{cloud_url}/api/v1/hub/device/whatsapp/disconnect/{phone_number_id}/",
                    json={},
                    headers={"X-Hub-Token": auth_token, "Content-Type": "application/json"},
                )

        # Deactivate local employee link if exists
        async with atomic(db):
            links = await _q(EmployeeWhatsAppLink, db, hub_id).filter(
                EmployeeWhatsAppLink.phone_number_id == phone_number_id,
            ).all()
            for link in links:
                link.is_active = False
            await db.flush()

    except Exception:
        logger.exception("Failed to disconnect WhatsApp number %s", phone_number_id)

    return htmx_redirect("/m/whatsapp_inbox/settings")


# ==============================================================================
# WhatsApp Templates
# ==============================================================================

@router.get("/templates")
@htmx_view(module_id="whatsapp_inbox", view_id="templates")
async def templates_list(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    hub_id: HubId,
    q: str = "",
    page: int = 1,
    per_page: int = 25,
):
    """List WhatsApp templates with search and pagination."""
    query = _q(WhatsAppTemplate, db, hub_id)

    if q:
        query = query.filter(
            WhatsAppTemplate.name.ilike(f"%{q}%"),
        )

    total = await query.count()
    templates = await query.order_by(
        WhatsAppTemplate.name.asc(),
    ).offset((page - 1) * per_page).limit(per_page).all()

    return {
        "templates": templates,
        "total": total,
        "page": page,
        "per_page": per_page,
        "has_next": (page * per_page) < total,
        "q": q,
    }


@router.get("/templates/new")
@htmx_view(module_id="whatsapp_inbox", view_id="template_new")
async def template_new_form(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    hub_id: HubId,
):
    """New WhatsApp template form."""
    return {"template": None}


@router.post("/templates/new")
async def template_create(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    hub_id: HubId,
):
    """Create a new WhatsApp template."""
    form = await request.form()
    name = form.get("name", "").strip()
    language = form.get("language", "es").strip()
    category = form.get("category", "UTILITY").strip()
    header = form.get("header", "").strip()
    body = form.get("body", "").strip()
    footer = form.get("footer", "").strip()
    variables_raw = form.get("variables", "").strip()

    if not name:
        add_message(request, "error", "Template name is required")
        return htmx_redirect("/m/whatsapp_inbox/templates/new")

    if category not in ("MARKETING", "UTILITY", "AUTHENTICATION"):
        add_message(request, "error", "Invalid category")
        return htmx_redirect("/m/whatsapp_inbox/templates/new")

    variables = [v.strip() for v in variables_raw.split(",") if v.strip()] if variables_raw else []

    async with atomic(db) as session:
        template = WhatsAppTemplate(
            hub_id=hub_id,
            name=name,
            language=language,
            category=category,
            header=header,
            body=body,
            footer=footer,
            variables=variables,
            meta_status="pending",
            is_active=True,
        )
        session.add(template)
        await session.flush()

    add_message(request, "success", f"Template '{name}' created")
    return htmx_redirect(f"/m/whatsapp_inbox/templates/{template.id}")


@router.get("/templates/{pk}")
@htmx_view(module_id="whatsapp_inbox", view_id="template_detail")
async def template_detail(
    request: Request,
    pk: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    hub_id: HubId,
):
    """WhatsApp template detail / edit form."""
    template = await _q(WhatsAppTemplate, db, hub_id).get(pk)
    if template is None:
        return JSONResponse({"error": "Template not found"}, status_code=404)

    return {"template": template}


@router.post("/templates/{pk}")
async def template_update(
    request: Request,
    pk: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    hub_id: HubId,
):
    """Update a WhatsApp template."""
    template = await _q(WhatsAppTemplate, db, hub_id).get(pk)
    if template is None:
        return JSONResponse({"error": "Template not found"}, status_code=404)

    form = await request.form()
    name = form.get("name", "").strip()
    language = form.get("language", "es").strip()
    category = form.get("category", "UTILITY").strip()
    header = form.get("header", "").strip()
    body = form.get("body", "").strip()
    footer = form.get("footer", "").strip()
    variables_raw = form.get("variables", "").strip()

    if not name:
        add_message(request, "error", "Template name is required")
        return htmx_redirect(f"/m/whatsapp_inbox/templates/{pk}")

    if category not in ("MARKETING", "UTILITY", "AUTHENTICATION"):
        add_message(request, "error", "Invalid category")
        return htmx_redirect(f"/m/whatsapp_inbox/templates/{pk}")

    variables = [v.strip() for v in variables_raw.split(",") if v.strip()] if variables_raw else []

    # Check if content changed — reset meta_status to pending
    content_changed = (
        template.name != name
        or template.language != language
        or template.category != category
        or template.header != header
        or template.body != body
        or template.footer != footer
    )

    async with atomic(db) as session:
        template.name = name
        template.language = language
        template.category = category
        template.header = header
        template.body = body
        template.footer = footer
        template.variables = variables
        if content_changed:
            template.meta_status = "pending"
        await session.flush()

    add_message(request, "success", f"Template '{name}' saved")
    if content_changed:
        add_message(request, "info", "Meta status reset to pending — resubmit to Meta Business Manager for approval")
    return htmx_redirect(f"/m/whatsapp_inbox/templates/{pk}")


@router.post("/templates/{pk}/delete")
async def template_delete(
    request: Request,
    pk: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    hub_id: HubId,
):
    """Delete a WhatsApp template."""
    template = await _q(WhatsAppTemplate, db, hub_id).get(pk)
    if template is None:
        return JSONResponse({"error": "Template not found"}, status_code=404)

    name = template.name
    async with atomic(db) as session:
        await session.delete(template)

    add_message(request, "success", f"Template '{name}' deleted")
    return htmx_redirect("/m/whatsapp_inbox/templates")


@router.post("/templates/{pk}/sync-meta")
async def template_sync_meta(
    request: Request,
    pk: uuid.UUID,
    db: DbSession,
    user: CurrentUser,
    hub_id: HubId,
):
    """Sync a WhatsApp template with Meta Business Manager (placeholder)."""
    template = await _q(WhatsAppTemplate, db, hub_id).get(pk)
    if template is None:
        return JSONResponse({"error": "Template not found"}, status_code=404)

    logger.info(
        "[whatsapp_inbox] sync-meta called for template %s (%s) — not yet implemented",
        template.id,
        template.name,
    )

    add_message(request, "info", "Sync with Meta is not yet implemented. Coming soon.")
    return htmx_redirect(f"/m/whatsapp_inbox/templates/{pk}")
