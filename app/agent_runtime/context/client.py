from __future__ import annotations

from typing import Any

from .workflow_command import normalize_workflow_command
from .workflow_reply import normalize_workflow_reply_context


def sanitize_agent_client_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    context: dict[str, Any] = {}
    for key, max_length in (
        ("source", 80),
        ("locale", 35),
        ("timezone", 80),
        ("message_sent_at", 80),
    ):
        text = _text(value.get(key), max_length=max_length)
        if text:
            context[key] = text

    workflow_reply = normalize_workflow_reply_context(value.get("workflow_reply"))
    if workflow_reply:
        context["workflow_reply"] = workflow_reply
    workflow_command = normalize_workflow_command(value.get("workflow_command"))
    if workflow_command:
        context["workflow_command"] = workflow_command
    return context


def project_agent_client_context(value: Any) -> dict[str, Any]:
    context = sanitize_agent_client_context(value)
    return {
        key: context[key]
        for key in ("locale", "timezone", "message_sent_at")
        if key in context
    }


def _text(value: Any, *, max_length: int) -> str:
    return value.strip()[:max_length] if isinstance(value, str) else ""
