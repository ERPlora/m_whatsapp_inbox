"""
WhatsApp Inbox REST API endpoints — FastAPI router.

Mounted at /api/v1/m/whatsapp_inbox/ by ModuleRuntime.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()

# REST API endpoints can be added here as needed.
# The module primarily uses HTMX views (routes.py) for the browser interface
# and AI tools (ai_tools.py) for the assistant interface.
