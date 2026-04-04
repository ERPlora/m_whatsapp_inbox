"""AI Assistant context provider for WhatsApp Inbox module.

Provides contextual information about the module's current state
so the AI assistant can give relevant answers.
"""

from __future__ import annotations

from typing import Any

CONTEXT = """
## Module Knowledge: WhatsApp Inbox

### Architecture

Customer WhatsApp -> Meta Cloud API -> Cloud webhook -> SQS -> Lambda worker -> Hub DB
Hub UI shows: Inbox (conversations), Requests (parsed solicitations), Settings

### Models

**WhatsAppInboxSettings** (singleton per hub)
- account_mode: shared (one number for team) | per_employee (each salesperson has own number)
- auto_reply_enabled (bool), approval_mode: auto | manual, require_confirmation (bool)
- input_modules (list) -- modules queried for GPT catalog context (inventory, services, catalog)
- output_modules (list) -- modules that handle confirmed requests (orders, reservations, appointments)
- request_schema (JSONField), gpt_system_prompt (custom business info for GPT)
- greeting_message, out_of_hours_message, auto_close_hours

**WhatsAppConversation**
- customer (FK), assigned_to (FK -> LocalUser, for per-employee mode)
- contact_name, contact_phone, status: active | waiting_confirmation | closed
- unread_count, last_message_at

**WhatsAppMessage**
- conversation (FK), direction: inbound | outbound
- message_type: text | image | audio | document | location | interactive
- body, status: received | sent | delivered | read | failed

**InboxRequest** (dynamic schema)
- conversation (FK), reference_number (REQ-YYYYMMDD-XXXX)
- request_type: order | reservation | appointment | quote | transport | custom
- status: pending_review | confirmed | rejected | fulfilled | cancelled
- data (JSONField -- structured fields parsed by GPT), raw_summary, confidence_score
- assigned_to (FK), linked_module, linked_object_id (UUID of created object)

### Decoupled Output Module Interface

Each output module owns its WhatsApp integration via `{module_id}/whatsapp.py`:
- `check_availability(hub_id, data)` -- verify if request can be fulfilled
- `create_from_request(hub_id, data, customer, conversation)` -- create the object
- `get_context_for_bot(hub_id)` -- provide context text for GPT prompt

Dispatch: actions.py maps request_type -> module_id -> `import_module(f'{module_id}.whatsapp')`

Implemented now: commands/orders (always available) and reservations (slot capacity).
Pending implementation: appointments still needs its `appointments/whatsapp.py` bridge.

### Key flows

1. **Approve request**: approve_whatsapp_request -> calls module's check_availability -> create_from_request -> links InboxRequest to created object
2. **Reject request**: reject_whatsapp_request -> status -> rejected
3. **Fulfill request**: fulfill_whatsapp_request -> status -> fulfilled (for already-created objects)
4. **Assign conversation**: assign_whatsapp_conversation -> reassign to different employee (per-employee mode)
5. **Auto-reply**: Lambda receives message -> GPT parses -> creates InboxRequest if request detected -> confirmation flow
"""


async def get_context(request: Any) -> dict:
    """Return context dict for the AI assistant.

    Called when the assistant needs context about this module.
    Keep it lightweight -- only include counts and key settings.
    """
    from app.core.db.query import HubQuery
    from .models import WhatsAppInboxSettings, WhatsAppConversation, InboxRequest

    db = request.state.db
    hub_id = request.state.hub_id

    settings = await HubQuery(WhatsAppInboxSettings, db, hub_id).first()
    if not settings:
        return {
            "module": "whatsapp_inbox",
            "enabled": False,
            "auto_reply": False,
            "approval_mode": "auto",
            "input_modules": [],
            "output_modules": [],
            "conversations": {"total": 0, "active": 0, "unread": 0},
            "requests": {"total": 0, "pending_review": 0, "confirmed": 0},
        }

    conv_q = HubQuery(WhatsAppConversation, db, hub_id)
    conv_total = await conv_q.count()
    conv_active = await conv_q.filter(WhatsAppConversation.status == "active").count()
    conv_unread = await conv_q.filter(WhatsAppConversation.unread_count > 0).count()

    req_q = HubQuery(InboxRequest, db, hub_id)
    req_total = await req_q.count()
    req_pending = await req_q.filter(InboxRequest.status == "pending_review").count()
    req_confirmed = await req_q.filter(InboxRequest.status == "confirmed").count()

    return {
        "module": "whatsapp_inbox",
        "enabled": settings.is_enabled,
        "auto_reply": settings.auto_reply_enabled,
        "approval_mode": settings.approval_mode,
        "input_modules": settings.input_modules or [],
        "output_modules": settings.output_modules or [],
        "conversations": {
            "total": conv_total,
            "active": conv_active,
            "unread": conv_unread,
        },
        "requests": {
            "total": req_total,
            "pending_review": req_pending,
            "confirmed": req_confirmed,
        },
    }


SOPS = [
    {
        "id": "review_whatsapp_requests",
        "triggers": {
            "es": ["solicitudes whatsapp", "peticiones pendientes", "revisar pedidos whatsapp"],
            "en": ["whatsapp requests", "pending requests", "review whatsapp orders"],
        },
        "description": {"es": "Revisar solicitudes pendientes de WhatsApp", "en": "Review pending WhatsApp requests"},
        "steps": [
            {"tool": "list_whatsapp_requests", "args": {"status": "pending_review"}, "description": "List pending requests"},
        ],
        "modules_required": ["whatsapp_inbox"],
    },
    {
        "id": "whatsapp_inbox_status",
        "triggers": {
            "es": ["estado whatsapp", "inbox whatsapp", "conversaciones activas"],
            "en": ["whatsapp status", "whatsapp inbox", "active conversations"],
        },
        "description": {"es": "Ver estado del inbox de WhatsApp", "en": "View WhatsApp inbox status"},
        "steps": [
            {"tool": "list_whatsapp_conversations", "args": {"status": "active"}, "description": "List active conversations"},
        ],
        "modules_required": ["whatsapp_inbox"],
    },
    {
        "id": "configure_whatsapp_inbox",
        "triggers": {
            "es": [
                "configurar whatsapp inbox",
                "usar whatsapp para reservas",
                "usar whatsapp para pedidos",
                "usar whatsapp para citas",
            ],
            "en": [
                "configure whatsapp inbox",
                "use whatsapp for reservations",
                "use whatsapp for orders",
                "use whatsapp for appointments",
            ],
        },
        "description": {
            "es": "Preparar WhatsApp Inbox para un caso de uso concreto",
            "en": "Prepare WhatsApp Inbox for a specific use case",
        },
        "steps": [
            {"tool": "list_whatsapp_setup_options", "args": {}, "description": "List available setup options"},
        ],
        "modules_required": ["whatsapp_inbox"],
    },
]
