"""DEPRECATED: code has been absorbed into messaging.drivers.whatsapp_business.

This module is now a no-op shim kept only to preserve MODULE_ID registration
during the transition. Uninstall it after migrating existing hubs.

What moved where:
  - Webhook handler  → messaging.drivers.whatsapp_business.driver (normalize_webhook)
                       messaging.webhooks.router (central dispatch)
  - Meta API client  → messaging.drivers.whatsapp_business.driver (send_text_message, etc.)
  - Bot / GPT parser → messaging.drivers.whatsapp_business (gpt_parser placeholder)
  - Models           → whatsapp_inbox tables remain for backward compat;
                       new data goes into messaging_conversation + messaging_inbound_message

New integrations must use:
    from messaging.channels.registry import get_driver
    driver = get_driver("whatsapp")
"""

import warnings

warnings.warn(
    "whatsapp_inbox is deprecated — use messaging module (channel_id='whatsapp'). "
    "Webhook endpoint: /webhooks/messaging/whatsapp/<account_id>. "
    "This shim will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)
