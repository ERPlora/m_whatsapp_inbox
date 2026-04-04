"""
WhatsApp Inbox module UI slots.

Registers slot contributions for cross-module UI extensibility.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.slots import SlotRegistry


def register_slots(slots: SlotRegistry, module_id: str) -> None:
    """
    Register UI slot contributions for the whatsapp_inbox module.

    Called by ModuleRuntime during module load.
    """
    # No slot contributions at this time.
