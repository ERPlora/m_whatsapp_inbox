"""
WhatsApp Business Cloud API driver — REAL implementation.

Repatriated from messaging.drivers.whatsapp_business back to whatsapp_inbox.

Supports:
- send(): POST to Meta Graph API /messages endpoint
- normalize_webhook(): parse Meta Cloud API webhook payload → list[InboundMessage]
- validate_credentials(): check access_token + phone_number_id are present
- mark_as_read(): send read receipt (called internally after processing)

Meta API version: v21.0
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

META_API_VERSION = "v21.0"
META_API_BASE = f"https://graph.facebook.com/{META_API_VERSION}"


# ---------------------------------------------------------------------------
# ChannelDriver base — inlined from messaging.channels.base
# ---------------------------------------------------------------------------

class Capability(StrEnum):
    TEXT = "text"
    MEDIA = "media"
    TEMPLATES = "templates"
    BUTTONS = "buttons"
    THREADING = "threading"
    TYPING = "typing"
    REACTIONS = "reactions"


@dataclass
class Attachment:
    filename: str
    content_type: str
    url: str | None = None
    size: int | None = None


@dataclass
class InboundMessage:
    channel_id: str
    account_id: str
    external_thread_id: str
    external_message_id: str
    from_identifier: str          # phone, email, handle
    body: str
    attachments: list[Attachment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    sent_at: str | None = None    # ISO 8601


@dataclass
class OutboundMessage:
    channel_id: str
    account_id: str
    to_identifier: str
    body: str
    attachments: list[Attachment] = field(default_factory=list)
    template_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeliveryReceipt:
    external_message_id: str | None
    status: str                   # "sent" | "queued" | "failed"
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class ChannelDriver(ABC):
    """Abstract base for every messaging channel."""

    channel_id: str = ""
    display_name: str = ""
    icon: str = ""
    capabilities: set[Capability] = set()

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> DeliveryReceipt: ...

    @abstractmethod
    async def normalize_webhook(
        self,
        payload: dict,
        headers: dict | None = None,
    ) -> list[InboundMessage]: ...

    async def validate_credentials(self, config: dict) -> bool:
        return True

    async def sync_inbox(self, account: Any) -> None:
        return None

    def supports_push(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# WhatsAppDriver
# ---------------------------------------------------------------------------

class WhatsAppDriver(ChannelDriver):
    """
    WhatsApp Business Cloud API driver.

    Real implementation — handles Meta webhook normalization and message send.
    Credentials (access_token, phone_number_id) come from WhatsAppInboxSettings
    or the account object (per-account credentials).
    """

    channel_id = "whatsapp"
    display_name = "WhatsApp Business"
    icon = "logo-whatsapp"
    capabilities = {
        Capability.TEXT,
        Capability.MEDIA,
        Capability.TEMPLATES,
        Capability.BUTTONS,
        Capability.THREADING,
        Capability.TYPING,
        Capability.REACTIONS,
    }

    # -------------------------------------------------------------------------
    # send
    # -------------------------------------------------------------------------

    async def send(self, msg: OutboundMessage) -> DeliveryReceipt:
        """Send a WhatsApp text message via Meta Cloud API."""
        creds = await self._resolve_credentials(msg.account_id, msg.metadata)
        if not creds:
            logger.error(
                "[WhatsAppDriver] No credentials found for account_id=%s", msg.account_id,
            )
            return DeliveryReceipt(
                external_message_id=None,
                status="failed",
                error="Missing WhatsApp credentials",
            )

        access_token = creds["access_token"]
        phone_number_id = creds["phone_number_id"]

        url = f"{META_API_BASE}/{phone_number_id}/messages"
        payload = json.dumps({
            "messaging_product": "whatsapp",
            "to": msg.to_identifier,
            "type": "text",
            "text": {"body": msg.body},
        }).encode("utf-8")

        result = _meta_post(url, access_token, payload)

        if result is None:
            return DeliveryReceipt(
                external_message_id=None,
                status="failed",
                error="Meta API request failed",
            )

        external_id = None
        messages = result.get("messages", [])
        if messages and isinstance(messages, list):
            external_id = messages[0].get("id")

        return DeliveryReceipt(
            external_message_id=external_id,
            status="sent",
            raw=result,
        )

    # -------------------------------------------------------------------------
    # normalize_webhook
    # -------------------------------------------------------------------------

    async def normalize_webhook(
        self,
        payload: dict,
        headers: dict | None = None,
    ) -> list[InboundMessage]:
        """Parse a Meta WhatsApp Cloud API webhook payload into InboundMessage list."""
        messages: list[InboundMessage] = []

        if payload.get("object") != "whatsapp_business_account":
            return messages

        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                if change.get("field") != "messages":
                    continue

                value = change.get("value", {})
                metadata = value.get("metadata", {})
                phone_number_id = metadata.get("phone_number_id", "")

                contacts = {
                    c["wa_id"]: c.get("profile", {}).get("name", c["wa_id"])
                    for c in value.get("contacts", [])
                    if "wa_id" in c
                }

                for msg in value.get("messages", []):
                    inbound = _parse_message(msg, contacts, phone_number_id)
                    if inbound is not None:
                        messages.append(inbound)

        return messages

    # -------------------------------------------------------------------------
    # validate_credentials
    # -------------------------------------------------------------------------

    async def validate_credentials(self, config: dict) -> bool:
        """Check that required WhatsApp credentials are present."""
        required = ("whatsapp_api_token", "whatsapp_phone_id")
        return all(config.get(k) for k in required)

    # -------------------------------------------------------------------------
    # supports_push / sync_inbox
    # -------------------------------------------------------------------------

    def supports_push(self) -> bool:
        """WhatsApp uses webhook push — no pull sync needed."""
        return True

    async def sync_inbox(self, account: Any) -> None:
        """WhatsApp is push-only; no IMAP-style polling."""
        return None

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    async def _resolve_credentials(
        self, account_id: str, metadata: dict,
    ) -> dict | None:
        """Resolve credentials from metadata or WhatsAppInboxSettings."""
        if metadata.get("access_token") and metadata.get("phone_number_id"):
            return {
                "access_token": metadata["access_token"],
                "phone_number_id": metadata["phone_number_id"],
            }

        # Fallback: load from WhatsAppInboxSettings
        try:
            from app.core.db.session import get_sync_session
            from whatsapp_inbox.models import WhatsAppInboxSettings

            with get_sync_session() as db:
                from sqlalchemy import select
                import uuid as _uuid

                try:
                    hub_uuid = _uuid.UUID(account_id)
                except ValueError:
                    return None

                row = db.execute(
                    select(WhatsAppInboxSettings).where(
                        WhatsAppInboxSettings.hub_id == hub_uuid,
                        WhatsAppInboxSettings.is_deleted.is_(False),
                    )
                ).scalar_one_or_none()

                # WhatsAppInboxSettings doesn't store API tokens directly —
                # those live in environment or per-account config.
                # Return None to signal caller should use env-based credentials.
                if row:
                    return None
        except Exception:
            logger.debug(
                "[WhatsAppDriver] Could not load settings for account_id=%s", account_id,
            )

        return None


# -----------------------------------------------------------------------------
# Module-level helpers (no self)
# -----------------------------------------------------------------------------

def send_text_message(
    access_token: str, phone_number_id: str, to_number: str, text: str,
) -> dict | None:
    """Send a text message to a WhatsApp number."""
    url = f"{META_API_BASE}/{phone_number_id}/messages"
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": text},
    }).encode("utf-8")
    return _meta_post(url, access_token, payload)


def send_interactive_buttons(
    access_token: str,
    phone_number_id: str,
    to_number: str,
    body_text: str,
    buttons: list[dict] | None = None,
) -> dict | None:
    """Send an interactive button message."""
    if buttons is None:
        buttons = [
            {"type": "reply", "reply": {"id": "confirm", "title": "Confirmar"}},
            {"type": "reply", "reply": {"id": "cancel", "title": "Cancelar"}},
        ]
    url = f"{META_API_BASE}/{phone_number_id}/messages"
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text},
            "action": {"buttons": buttons},
        },
    }).encode("utf-8")
    return _meta_post(url, access_token, payload)


def mark_as_read(
    access_token: str, phone_number_id: str, message_id: str,
) -> dict | None:
    """Send a read receipt to Meta (shows blue checkmarks on customer's phone)."""
    url = f"{META_API_BASE}/{phone_number_id}/messages"
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }).encode("utf-8")
    return _meta_post(url, access_token, payload)


def _meta_post(url: str, access_token: str, payload: bytes) -> dict | None:
    """Make an authenticated POST request to Meta Graph API."""
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8") if exc.fp else ""
        logger.error("[WhatsAppDriver] Meta API error %d: %s", exc.code, error_body)
        return None
    except Exception:
        logger.exception("[WhatsAppDriver] Meta API request failed: %s", url)
        return None


def _parse_message(
    msg: dict,
    contacts: dict[str, str],
    phone_number_id: str,
) -> InboundMessage | None:
    """Convert a single Meta webhook message object to InboundMessage."""
    msg_type = msg.get("type", "")
    wa_id = msg.get("from", "")
    wamid = msg.get("id", "")
    timestamp = msg.get("timestamp", "")

    if not wa_id or not wamid:
        return None

    if msg_type == "system":
        return None

    sender_name = contacts.get(wa_id, wa_id)

    body = ""
    attachments: list[Attachment] = []

    if msg_type == "text":
        body = msg.get("text", {}).get("body", "")
    elif msg_type in ("image", "document", "audio", "video", "sticker"):
        media_data = msg.get(msg_type, {})
        body = f"[{msg_type}]"
        if media_data.get("id"):
            attachments.append(Attachment(
                filename=media_data.get("filename", f"{msg_type}_{wamid}"),
                content_type=media_data.get("mime_type", f"{msg_type}/*"),
                url=None,
            ))
    elif msg_type == "interactive":
        interactive = msg.get("interactive", {})
        interactive_type = interactive.get("type", "")
        if interactive_type == "button_reply":
            reply = interactive.get("button_reply", {})
            body = reply.get("title", "")
        elif interactive_type == "list_reply":
            reply = interactive.get("list_reply", {})
            body = reply.get("title", "")
        else:
            body = f"[interactive: {interactive_type}]"
    elif msg_type == "location":
        loc = msg.get("location", {})
        lat = loc.get("latitude", "")
        lon = loc.get("longitude", "")
        body = f"[location: {lat},{lon}]"
    elif msg_type == "contacts":
        body = "[contacts]"
    elif msg_type == "reaction":
        emoji = msg.get("reaction", {}).get("emoji", "")
        body = f"[reaction: {emoji}]"
    else:
        logger.debug("[WhatsAppDriver] Unhandled message type: %s", msg_type)
        body = f"[{msg_type}]"

    sent_at: str | None = None
    if timestamp:
        try:
            sent_at = datetime.fromtimestamp(int(timestamp), tz=UTC).isoformat()
        except (ValueError, TypeError):
            sent_at = timestamp

    return InboundMessage(
        channel_id="whatsapp",
        account_id=phone_number_id,
        external_thread_id=wa_id,
        external_message_id=wamid,
        from_identifier=wa_id,
        body=body,
        attachments=attachments,
        metadata={
            "sender_name": sender_name,
            "phone_number_id": phone_number_id,
            "message_type": msg_type,
        },
        sent_at=sent_at,
    )
