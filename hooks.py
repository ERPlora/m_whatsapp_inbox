"""
WhatsApp Inbox module hook registrations.

Registers actions and filters on the HookRegistry during module load.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.hooks.registry import HookRegistry

MODULE_ID = "whatsapp_inbox"


def register_hooks(hooks: HookRegistry, module_id: str) -> None:
    """
    Register hooks for the whatsapp_inbox module.

    Called by ModuleRuntime during module load.
    """
    # No hooks registered at this time.
