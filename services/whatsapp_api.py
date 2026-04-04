"""WhatsApp Cloud API client.

Sends messages to customers via Meta Cloud API.
The access_token is stored in the messaging module's settings.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

META_API_VERSION = "v21.0"
META_API_BASE = f"https://graph.facebook.com/{META_API_VERSION}"


def send_text_message(
    access_token: str, phone_number_id: str, to_number: str, text: str,
) -> dict | None:
    """Send a text message to a WhatsApp number.

    Args:
        access_token: Meta API access token
        phone_number_id: WhatsApp Business phone number ID
        to_number: Recipient phone number (international format)
        text: Message body

    Returns:
        dict with Meta API response, or None on error
    """
    url = f"{META_API_BASE}/{phone_number_id}/messages"
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": text},
    }).encode("utf-8")

    return _make_request(url, access_token, payload)


def send_interactive_buttons(
    access_token: str,
    phone_number_id: str,
    to_number: str,
    body_text: str,
    buttons: list[dict] | None = None,
) -> dict | None:
    """Send an interactive message with reply buttons.

    Args:
        access_token: Meta API access token
        phone_number_id: WhatsApp Business phone number ID
        to_number: Recipient phone number
        body_text: Message body text
        buttons: List of dicts with 'id' and 'title' keys.
                 Defaults to Confirm/Cancel.

    Returns:
        dict with Meta API response, or None on error
    """
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

    return _make_request(url, access_token, payload)


def mark_as_read(
    access_token: str, phone_number_id: str, message_id: str,
) -> dict | None:
    """Mark a message as read (sends blue checkmarks).

    Args:
        access_token: Meta API access token
        phone_number_id: WhatsApp Business phone number ID
        message_id: Meta message ID to mark as read
    """
    url = f"{META_API_BASE}/{phone_number_id}/messages"
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }).encode("utf-8")

    return _make_request(url, access_token, payload)


def _make_request(url: str, access_token: str, payload: bytes) -> dict | None:
    """Make an authenticated POST request to Meta API."""
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
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else ""
        logger.error("Meta API error %d: %s", e.code, error_body)
        return None
    except Exception:
        logger.exception("Meta API request failed: %s", url)
        return None
