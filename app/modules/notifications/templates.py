from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationTemplate:
    category: str
    title: str
    body: str


# Inbox templates are separate from the generic lock-screen envelope. Locale
# selection is centralized; unsupported locales use English for now.
TEMPLATES: dict[str, NotificationTemplate] = {}
SERVICE_CATEGORIES = ("agent_updates",)
