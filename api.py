"""
WhatsApp Inbox REST API endpoints — FastAPI router.

Mounted at /api/v1/m/whatsapp_inbox/ by ModuleRuntime.

Endpoints:
- GET  /webhooks/meta/{account_id}  — Meta webhook verification challenge
- POST /webhooks/meta/{account_id}  — Meta Cloud API incoming messages
- POST /webhook/incoming/           — Lambda whatsapp-worker sends processed messages here
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select

from app.core.dependencies import DbSession

from .drivers.webhook import verify_signature, verify_webhook
from .drivers.whatsapp_business import WhatsAppDriver
from .models import (
    InboxRequest,
    WhatsAppConversation,
    WhatsAppMessage,
)
from .schemas import IncomingWebhookPayload

logger = logging.getLogger(__name__)

router = APIRouter()

# Shared secret for Lambda authentication
_WHATSAPP_WEBHOOK_SECRET = os.environ.get("WHATSAPP_WEBHOOK_SECRET", "")
_WHATSAPP_APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "")


def _check_auth(request: Request) -> bool:
    """Validate X-Whatsapp-Secret header."""
    if not _WHATSAPP_WEBHOOK_SECRET:
        return True  # Dev mode: no secret set
    return request.headers.get("X-Whatsapp-Secret", "") == _WHATSAPP_WEBHOOK_SECRET


# ---------------------------------------------------------------------------
# GET /webhooks/meta/{account_id} — Meta verification challenge
# ---------------------------------------------------------------------------

@router.get("/webhooks/meta/{account_id}")
async def meta_webhook_verify(request: Request, account_id: str) -> PlainTextResponse:
    """
    Handle Meta Cloud API webhook GET verification.

    Meta sends: hub.mode=subscribe, hub.verify_token, hub.challenge
    We respond with hub.challenge if the verify_token matches.
    """
    return await verify_webhook(request, account_id)


# ---------------------------------------------------------------------------
# POST /webhooks/meta/{account_id} — Meta Cloud API incoming messages
# ---------------------------------------------------------------------------

@router.post("/webhooks/meta/{account_id}")
async def meta_webhook_incoming(
    request: Request,
    account_id: str,
    db: DbSession,
) -> JSONResponse:
    """
    Receive raw WhatsApp webhook POST from Meta Cloud API.

    Validates X-Hub-Signature-256 HMAC if WHATSAPP_APP_SECRET is set.
    Normalizes payload via WhatsAppDriver and persists conversations + messages.
    """
    body = await request.body()

    # Signature validation (skip in dev if no app secret configured)
    if _WHATSAPP_APP_SECRET:
        sig = request.headers.get("X-Hub-Signature-256", "")
        if not verify_signature(body, sig, _WHATSAPP_APP_SECRET):
            logger.warning(
                "[WhatsApp webhook] Invalid signature for account %s", account_id,
            )
            return JSONResponse({"error": "invalid signature"}, status_code=403)

    try:
        import json as _json
        payload = _json.loads(body)
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    driver = WhatsAppDriver()
    try:
        inbound_messages = await driver.normalize_webhook(payload)
    except Exception:
        logger.exception("[WhatsApp webhook] normalize_webhook failed for account %s", account_id)
        return JSONResponse({"error": "parse error"}, status_code=500)

    if not inbound_messages:
        # Delivery receipts / status updates — acknowledge immediately
        return JSONResponse({"status": "ok", "processed": 0})

    created_count = 0
    for inbound in inbound_messages:
        try:
            hub_uuid = await _resolve_hub_id_from_account(account_id, db)
            if not hub_uuid:
                logger.warning(
                    "[WhatsApp webhook] Could not resolve hub for account_id=%s", account_id,
                )
                continue

            # Idempotency check
            existing = await db.execute(
                select(WhatsAppMessage).where(
                    WhatsAppMessage.hub_id == hub_uuid,
                    WhatsAppMessage.wa_message_id == inbound.external_message_id,
                    WhatsAppMessage.is_deleted.is_(False),
                )
            )
            if existing.scalar_one_or_none():
                continue

            conversation, _ = await _get_or_create_conversation(
                db,
                hub_uuid,
                _ConvData(
                    wa_contact_id=inbound.external_thread_id,
                    contact_name=inbound.metadata.get("sender_name", inbound.from_identifier),
                    contact_phone=inbound.from_identifier,
                    phone_number_id=inbound.metadata.get("phone_number_id", ""),
                    assigned_employee_id=None,
                ),
                assigned_employee_id=None,
            )

            message = WhatsAppMessage(
                hub_id=hub_uuid,
                conversation_id=conversation.id,
                direction="inbound",
                wa_message_id=inbound.external_message_id,
                message_type=inbound.metadata.get("message_type", "text"),
                body=inbound.body,
                status="received",
                extra_metadata=inbound.metadata,
            )
            db.add(message)

            conversation.last_message_at = datetime.now(datetime.UTC)
            conversation.unread_count = (conversation.unread_count or 0) + 1

            await db.flush()
            created_count += 1

        except Exception:
            logger.exception(
                "[WhatsApp webhook] Failed to persist message %s", inbound.external_message_id,
            )
            continue

    await db.commit()
    return JSONResponse({"status": "ok", "processed": created_count})


# ---------------------------------------------------------------------------
# Helpers for Meta webhook
# ---------------------------------------------------------------------------

class _ConvData:
    """Lightweight DTO for conversation lookup/create."""
    def __init__(
        self,
        wa_contact_id: str,
        contact_name: str,
        contact_phone: str,
        phone_number_id: str,
        assigned_employee_id,
    ):
        self.wa_contact_id = wa_contact_id
        self.contact_name = contact_name
        self.contact_phone = contact_phone
        self.phone_number_id = phone_number_id
        self.assigned_employee_id = assigned_employee_id


async def _resolve_hub_id_from_account(account_id: str, db: DbSession):
    """Resolve hub_id from a phone_number_id or account_id string.

    Currently uses the hub_id stored in WhatsAppInboxSettings that matches
    the phone_number_id. Falls back to treating account_id as a hub UUID.
    """
    try:
        return uuid.UUID(account_id)
    except ValueError:
        pass

    # Try looking up by phone_number_id in EmployeeWhatsAppLink
    try:
        from .models import EmployeeWhatsAppLink
        result = await db.execute(
            select(EmployeeWhatsAppLink).where(
                EmployeeWhatsAppLink.phone_number_id == account_id,
                EmployeeWhatsAppLink.is_active.is_(True),
            ).limit(1)
        )
        link = result.scalar_one_or_none()
        if link:
            return link.hub_id
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# POST /webhook/incoming/ — Lambda sends processed messages here
# ---------------------------------------------------------------------------

@router.post("/webhook/incoming/")
@router.post("/webhook/incoming")
async def webhook_incoming(request: Request, db: DbSession):
    """
    Receive processed WhatsApp messages from Lambda worker.

    The Lambda handles: Cloud DB lookups, GPT calls, Meta API responses,
    usage tracking. This endpoint only handles Hub DB writes.

    Authentication: X-Whatsapp-Secret header.
    """
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=403)

    try:
        data = await request.json()
        payload = IncomingWebhookPayload(**data)
    except Exception as e:
        logger.error("Invalid webhook payload: %s", e)
        return JSONResponse({"error": "invalid payload", "detail": str(e)}, status_code=400)

    action = payload.action

    if action == "process_message":
        result = await _handle_process_message(db, payload)
    elif action == "status_update":
        result = await _handle_status_update(db, payload)
    elif action == "send_message":
        result = await _handle_send_message(db, payload)
    elif action == "button_reply":
        result = await _handle_button_reply(db, payload)
    else:
        return JSONResponse({"error": f"unknown action: {action}"}, status_code=400)

    await db.commit()
    return result


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

async def _handle_process_message(db: DbSession, payload: IncomingWebhookPayload) -> dict:
    """
    Handle a processed incoming WhatsApp message.

    Creates/updates conversation, stores message, creates InboxRequest if needed.
    """
    hub_uuid = uuid.UUID(payload.hub_id)
    conv_data = payload.conversation
    msg_data = payload.message

    if not conv_data or not msg_data:
        return JSONResponse(
            {"error": "conversation and message required for process_message"},
            status_code=400,
        )

    # 1. Check idempotency — skip if message already exists
    existing = await db.execute(
        select(WhatsAppMessage).where(
            WhatsAppMessage.hub_id == hub_uuid,
            WhatsAppMessage.wa_message_id == msg_data.wa_message_id,
            WhatsAppMessage.is_deleted == False,  # noqa: E712
        )
    )
    if existing.scalar_one_or_none():
        return {"status": "duplicate", "wa_message_id": msg_data.wa_message_id}

    # 2. Resolve employee assignment for per-employee mode
    assigned_employee_id = None
    if conv_data.assigned_employee_id:
        try:
            assigned_employee_id = uuid.UUID(conv_data.assigned_employee_id)
        except ValueError:
            pass

    # 3. Get or create conversation
    conversation, is_created = await _get_or_create_conversation(
        db, hub_uuid, conv_data, assigned_employee_id,
    )

    # 4. Store inbound message
    message = WhatsAppMessage(
        hub_id=hub_uuid,
        conversation_id=conversation.id,
        direction=msg_data.direction,
        wa_message_id=msg_data.wa_message_id,
        message_type=msg_data.message_type,
        body=msg_data.body,
        media_url=msg_data.media_url,
        status="received",
        extra_metadata=msg_data.metadata,
    )
    db.add(message)

    # 5. Update conversation
    conversation.last_message_at = datetime.now(datetime.UTC)
    conversation.unread_count = (conversation.unread_count or 0) + 1

    # 6. Handle GPT result
    gpt = payload.gpt_result
    settings = payload.settings_snapshot
    approval_mode = settings.get("approval_mode", "auto")
    require_confirmation = settings.get("require_confirmation", True)

    inbox_request_id = None

    if gpt and gpt.request_type and gpt.parsed_data:
        # GPT detected a request
        if approval_mode == "auto" and require_confirmation:
            # Waiting for confirmation — save pending request in context
            conversation.status = "waiting_confirmation"
            conversation.context = {
                "pending_request": {
                    "request_type": gpt.request_type,
                    "data": gpt.parsed_data,
                    "raw_summary": gpt.response_text,
                    "confidence": gpt.confidence,
                }
            }
        elif approval_mode == "auto" and not require_confirmation:
            # Auto-confirm
            inbox_request_id = await _create_inbox_request(
                db, hub_uuid, conversation.id,
                request_type=gpt.request_type,
                data=gpt.parsed_data,
                raw_summary=gpt.response_text,
                confidence=gpt.confidence,
                status="confirmed",
            )
        elif approval_mode == "manual":
            inbox_request_id = await _create_inbox_request(
                db, hub_uuid, conversation.id,
                request_type=gpt.request_type,
                data=gpt.parsed_data,
                raw_summary=gpt.response_text,
                confidence=gpt.confidence,
                status="pending_review",
            )

    # 7. Store outbound response message (if GPT generated one)
    if gpt and gpt.response_text:
        outbound = WhatsAppMessage(
            hub_id=hub_uuid,
            conversation_id=conversation.id,
            direction="outbound",
            wa_message_id=f"out_{uuid.uuid4().hex[:16]}",
            message_type="text",
            body=gpt.response_text,
            status="sent",
            metadata={},
        )
        db.add(outbound)

    await db.flush()

    return {
        "status": "ok",
        "conversation_id": str(conversation.id),
        "is_new_conversation": is_created,
        "inbox_request_id": str(inbox_request_id) if inbox_request_id else None,
    }


async def _handle_status_update(db: DbSession, payload: IncomingWebhookPayload) -> dict:
    """Update message delivery status (sent, delivered, read, failed)."""
    if not payload.wa_message_id or not payload.status:
        return JSONResponse(
            {"error": "wa_message_id and status required"}, status_code=400,
        )

    hub_uuid = uuid.UUID(payload.hub_id)
    result = await db.execute(
        select(WhatsAppMessage).where(
            WhatsAppMessage.hub_id == hub_uuid,
            WhatsAppMessage.wa_message_id == payload.wa_message_id,
            WhatsAppMessage.is_deleted == False,  # noqa: E712
        )
    )
    msg = result.scalar_one_or_none()
    if msg:
        msg.status = payload.status
        return {"status": "ok", "updated": True}

    return {"status": "ok", "updated": False, "reason": "message_not_found"}


async def _handle_send_message(db: DbSession, payload: IncomingWebhookPayload) -> dict:
    """Store an outbound message sent by Lambda (manual staff send)."""
    if not payload.outbound_message or not payload.conversation_id:
        return JSONResponse(
            {"error": "outbound_message and conversation_id required"}, status_code=400,
        )

    hub_uuid = uuid.UUID(payload.hub_id)
    msg_data = payload.outbound_message
    message = WhatsAppMessage(
        hub_id=hub_uuid,
        conversation_id=uuid.UUID(payload.conversation_id),
        direction="outbound",
        wa_message_id=msg_data.wa_message_id or f"out_{uuid.uuid4().hex[:16]}",
        message_type=msg_data.message_type,
        body=msg_data.body,
        status="sent",
        extra_metadata=msg_data.metadata,
    )
    db.add(message)

    return {"status": "ok", "message_id": str(message.id)}


async def _handle_button_reply(db: DbSession, payload: IncomingWebhookPayload) -> dict:
    """Handle confirmation/cancellation button reply."""
    hub_uuid = uuid.UUID(payload.hub_id)
    conv_data = payload.conversation
    msg_data = payload.message

    if not conv_data or not msg_data:
        return JSONResponse(
            {"error": "conversation and message required"}, status_code=400,
        )

    # Find the conversation
    result = await db.execute(
        select(WhatsAppConversation).where(
            WhatsAppConversation.hub_id == hub_uuid,
            WhatsAppConversation.wa_contact_id == conv_data.wa_contact_id,
            WhatsAppConversation.status == "waiting_confirmation",
            WhatsAppConversation.is_deleted == False,  # noqa: E712
        )
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        return {"status": "ok", "action": "no_pending_conversation"}

    context = conversation.context or {}
    pending = context.get("pending_request", {})
    button_id = msg_data.metadata.get("button_id", "")

    if button_id == "confirm" and pending:
        await _create_inbox_request(
            db, hub_uuid, conversation.id,
            request_type=pending.get("request_type", "custom"),
            data=pending.get("data", {}),
            raw_summary=pending.get("raw_summary", ""),
            confidence=pending.get("confidence", 0.0),
            status="confirmed",
        )

    # Reset conversation status
    conversation.status = "active"
    conversation.context = {}

    return {"status": "ok", "action": "confirmed" if button_id == "confirm" else "cancelled"}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _get_or_create_conversation(
    db: DbSession,
    hub_uuid: uuid.UUID,
    conv_data,
    assigned_employee_id: uuid.UUID | None = None,
) -> tuple:
    """Get or create a WhatsApp conversation. Returns (conversation, is_created)."""
    # Look for existing open conversation
    query = select(WhatsAppConversation).where(
        WhatsAppConversation.hub_id == hub_uuid,
        WhatsAppConversation.wa_contact_id == conv_data.wa_contact_id,
        WhatsAppConversation.status != "closed",
        WhatsAppConversation.is_deleted == False,  # noqa: E712
    )

    # In per-employee mode, also filter by phone_number_id
    if conv_data.phone_number_id and assigned_employee_id:
        query = query.where(
            WhatsAppConversation.phone_number_id == conv_data.phone_number_id,
        )

    query = query.order_by(WhatsAppConversation.last_message_at.desc()).limit(1)
    result = await db.execute(query)
    existing = result.scalar_one_or_none()

    if existing:
        return existing, False

    # Create new conversation
    conversation = WhatsAppConversation(
        hub_id=hub_uuid,
        wa_contact_id=conv_data.wa_contact_id,
        contact_name=conv_data.contact_name or conv_data.wa_contact_id,
        contact_phone=conv_data.contact_phone or conv_data.wa_contact_id,
        phone_number_id=conv_data.phone_number_id or "",
        assigned_to_id=assigned_employee_id,
        status="active",
        last_message_at=datetime.now(datetime.UTC),
        context={},
        unread_count=0,
    )
    db.add(conversation)
    await db.flush()

    return conversation, True


async def _create_inbox_request(
    db: DbSession,
    hub_uuid: uuid.UUID,
    conversation_id,
    request_type: str,
    data: dict,
    raw_summary: str,
    confidence: float,
    status: str,
) -> uuid.UUID:
    """Create an InboxRequest and return its ID."""
    ref = f"REQ-{datetime.now(datetime.UTC).strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"

    inbox_req = InboxRequest(
        hub_id=hub_uuid,
        conversation_id=conversation_id,
        reference_number=ref,
        request_type=request_type,
        status=status,
        data=data or {},
        raw_summary=raw_summary,
        confidence_score=confidence,
        confirmed_at=datetime.now(datetime.UTC) if status == "confirmed" else None,
    )
    db.add(inbox_req)
    await db.flush()

    return inbox_req.id
