"""
WhatsApp Inbox module manifest.

Generic dispatcher for WhatsApp Business messages with AI-powered auto-reply
and request management. Does NOT import target modules directly — uses
dynamic imports via import_module(f'{module_id}.whatsapp').
"""

from app.core.i18n import LazyString

# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------
MODULE_ID = "whatsapp_inbox"
MODULE_NAME = LazyString("WhatsApp Inbox", module_id="whatsapp_inbox")
MODULE_VERSION = "1.1.1"
MODULE_ICON = "logo-whatsapp"
MODULE_DESCRIPTION = LazyString(
    "Receive and process WhatsApp Business messages with AI-powered auto-reply and request management",
    module_id="whatsapp_inbox",
)
MODULE_AUTHOR = "ERPlora"
MODULE_CATEGORY = "marketing"

# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
HAS_MODELS = True
MIDDLEWARE = ""

# ---------------------------------------------------------------------------
# Menu (sidebar entry)
# ---------------------------------------------------------------------------
MENU = {
    "label": LazyString("WhatsApp Inbox", module_id="whatsapp_inbox"),
    "icon": "logo-whatsapp",
    "order": 71,
}

# ---------------------------------------------------------------------------
# Navigation tabs (bottom tabbar in module views)
# ---------------------------------------------------------------------------
NAVIGATION = [
    {
        "id": "inbox",
        "label": LazyString("Inbox", module_id="whatsapp_inbox"),
        "icon": "chatbubbles-outline",
        "view": "inbox",
    },
    {
        "id": "requests",
        "label": LazyString("Requests", module_id="whatsapp_inbox"),
        "icon": "clipboard-outline",
        "view": "requests",
    },
    {
        "id": "settings",
        "label": LazyString("Settings", module_id="whatsapp_inbox"),
        "icon": "settings-outline",
        "view": "settings",
    },
]

# ---------------------------------------------------------------------------
# Dependencies (other modules required to be active)
# ---------------------------------------------------------------------------
DEPENDENCIES: list[str] = ["messaging", "customers"]

# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
PERMISSIONS = [
    ("view_conversation", LazyString("View conversations", module_id="whatsapp_inbox")),
    ("send_message", LazyString("Send messages", module_id="whatsapp_inbox")),
    ("view_request", LazyString("View requests", module_id="whatsapp_inbox")),
    ("change_request", LazyString("Change requests", module_id="whatsapp_inbox")),
    ("delete_request", LazyString("Delete requests", module_id="whatsapp_inbox")),
    ("manage_settings", LazyString("Manage settings", module_id="whatsapp_inbox")),
    ("manage_connections", LazyString("Manage connections", module_id="whatsapp_inbox")),
]

ROLE_PERMISSIONS = {
    "admin": ["*"],
    "manager": [
        "change_request",
        "send_message",
        "view_conversation",
        "view_request",
    ],
    "employee": [
        "send_message",
        "view_conversation",
        "view_request",
    ],
}

# ---------------------------------------------------------------------------
# Scheduled tasks
# ---------------------------------------------------------------------------
SCHEDULED_TASKS: list[dict] = []

# ---------------------------------------------------------------------------
# Pricing (free module)
# ---------------------------------------------------------------------------
# PRICING = {"monthly": 0, "yearly": 0}
