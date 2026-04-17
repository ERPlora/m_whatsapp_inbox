"""
WhatsApp Business webhook handler.

Handles Meta Cloud API webhook verification (GET) and signature validation.
Repatriated from messaging.drivers.whatsapp_business.webhook.

Meta webhook payload format:
  {
    "object": "whatsapp_business_account",
    "entry": [{
      "id": "<WABA_ID>",
      "changes": [{
        "value": {
          "messaging_product": "whatsapp",
          "metadata": {"display_phone_number": "...", "phone_number_id": "..."},
          "contacts": [{"profile": {"name": "..."}, "wa_id": "..."}],
          "messages": [{
            "from": "<phone>",
            "id": "<wamid>",
            "timestamp": "...",
            "type": "text",
            "text": {"body": "..."}
          }]
        },
        "field": "messages"
      }]
    }]
  }
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import Request
from fastapi.responses import PlainTextResponse

logger = logging.getLogger(__name__)


async def verify_webhook(request: Request, account_id: str) -> PlainTextResponse:
    """Handle Meta webhook GET verification (hub.challenge handshake)."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and challenge:
        expected_token = await _get_verify_token(account_id)
        if expected_token and token != expected_token:
            logger.warning(
                "[WhatsApp webhook] Verify token mismatch for account %s", account_id,
            )
            return PlainTextResponse("Forbidden", status_code=403)
        logger.info(
            "[WhatsApp webhook] Verification successful for account %s", account_id,
        )
        return PlainTextResponse(challenge or "", status_code=200)

    return PlainTextResponse("Bad Request", status_code=400)


def verify_signature(body: bytes, signature_header: str, app_secret: str) -> bool:
    """Verify Meta's X-Hub-Signature-256 HMAC header.

    Args:
        body: raw request body bytes
        signature_header: value of X-Hub-Signature-256 header (e.g. 'sha256=abc...')
        app_secret: Meta app secret from settings

    Returns:
        True if signature is valid, False otherwise
    """
    if not signature_header or not app_secret:
        return False

    if not signature_header.startswith("sha256="):
        return False

    expected = signature_header[len("sha256="):]
    computed = hmac.new(
        app_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, computed)


async def _get_verify_token(account_id: str) -> str:
    """Retrieve the webhook verify token for this account from settings.

    Returns empty string if not configured (allows any token for backward compat).
    """
    try:
        from runtime.config.settings import get_settings
        settings = get_settings()
        return getattr(settings, "whatsapp_verify_token", "") or ""
    except Exception:
        logger.debug("[WhatsApp webhook] Could not load verify token settings")
        return ""
