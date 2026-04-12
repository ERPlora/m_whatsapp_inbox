"""
whatsapp_inbox — DEPRECATED.

This module is being absorbed into messaging.drivers.whatsapp_business.

Migration plan (Fase 5):
  - Full webhook handler → messaging.drivers.whatsapp_business.driver.WhatsAppDriver
  - GPT message parser → messaging.drivers.whatsapp_business.parser
  - All data stays in place; no data migration needed (drivers are stateless)

New integrations should use:
    from messaging.channels.registry import get_driver
    driver = get_driver("whatsapp")

Existing code continues to work until the shim is removed.
"""

import warnings

warnings.warn(
    "whatsapp_inbox is being absorbed into messaging.drivers.whatsapp_business. "
    "Use messaging.channels.registry.get_driver('whatsapp') for new integrations. "
    "This module will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)
