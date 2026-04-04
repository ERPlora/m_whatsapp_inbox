"""
WhatsApp Inbox module lifecycle hooks.

Called by ModuleRuntime during install/activate/deactivate/uninstall/upgrade.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def on_install(session: AsyncSession, hub_id: UUID) -> None:
    """Called after module installation + migration. Seed initial data if needed."""
    logger.info("WhatsApp Inbox module installed for hub %s", hub_id)


async def on_activate(session: AsyncSession, hub_id: UUID) -> None:
    """Called when module is activated."""
    logger.info("WhatsApp Inbox module activated for hub %s", hub_id)


async def on_deactivate(session: AsyncSession, hub_id: UUID) -> None:
    """Called when module is deactivated. Clean up caches."""
    logger.info("WhatsApp Inbox module deactivated for hub %s", hub_id)


async def on_uninstall(session: AsyncSession, hub_id: UUID) -> None:
    """Called before module uninstall. Final cleanup."""
    logger.info("WhatsApp Inbox module uninstalled for hub %s", hub_id)


async def on_upgrade(session: AsyncSession, hub_id: UUID, from_version: str, to_version: str) -> None:
    """Called when the module is updated. Run data migrations between versions."""
    logger.info(
        "WhatsApp Inbox module upgraded from %s to %s for hub %s",
        from_version, to_version, hub_id,
    )
