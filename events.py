"""
WhatsApp Inbox module event subscriptions.

Registers handlers on the AsyncEventBus during module load.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.events.bus import AsyncEventBus

logger = logging.getLogger(__name__)

MODULE_ID = "whatsapp_inbox"


def register_events(bus: AsyncEventBus, module_id: str) -> None:
    """
    Register event handlers for the whatsapp_inbox module.

    Called by ModuleRuntime during module load.
    """
    # No event subscriptions needed at this time.
    # The module is primarily event-driven through the Cloud webhook -> SQS -> Lambda pipeline.
