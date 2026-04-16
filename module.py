"""
WhatsApp Inbox module manifest.

Generic dispatcher for WhatsApp Business messages with AI-powered auto-reply
and request management. Does NOT import target modules directly — uses
dynamic imports via import_module(f'{module_id}.whatsapp').
"""


# ---------------------------------------------------------------------------
# Module identity
# ---------------------------------------------------------------------------
MODULE_ID = "whatsapp_inbox"
MODULE_NAME = "WhatsApp Inbox"
MODULE_VERSION = "2.1.1"
MODULE_ICON = "logo-whatsapp"
MODULE_DESCRIPTION = "Receive and process WhatsApp Business messages with AI-powered auto-reply and request management"
MODULE_AUTHOR = "ERPlora"

# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
HAS_MODELS = True
MIDDLEWARE = ""

# ---------------------------------------------------------------------------
# Menu (sidebar entry)
# ---------------------------------------------------------------------------
MENU = {
    "label": "WhatsApp Inbox",
    "icon": "logo-whatsapp",
    "order": 71,
}

# ---------------------------------------------------------------------------
# Navigation tabs (bottom tabbar in module views)
# ---------------------------------------------------------------------------
NAVIGATION = [
    {
        "id": "inbox",
        "label": "Inbox",
        "icon": "chatbubbles-outline",
        "view": "inbox",
    },
    {
        "id": "requests",
        "label": "Requests",
        "icon": "clipboard-outline",
        "view": "requests",
    },
    {
        "id": "templates",
        "label": "Templates",
        "icon": "document-text-outline",
        "view": "templates",
    },
    {
        "id": "settings",
        "label": "Settings",
        "icon": "settings-outline",
        "view": "settings",
    },
]

# ---------------------------------------------------------------------------
# Dependencies (other modules required to be active)
# ---------------------------------------------------------------------------
DEPENDENCIES: list[str] = ["customers"]

# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
PERMISSIONS = [
    ("view_conversation", "View conversations"),
    ("send_message", "Send messages"),
    ("view_request", "View requests"),
    ("change_request", "Change requests"),
    ("delete_request", "Delete requests"),
    ("manage_settings", "Manage settings"),
    ("manage_connections", "Manage connections"),
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
