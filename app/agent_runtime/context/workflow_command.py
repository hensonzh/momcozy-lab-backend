from __future__ import annotations

from typing import Any


PREGNANCY_PLAN_COMMAND_SCHEMA_VERSION = "pregnancy_plan_command.v1"
PREGNANCY_PLAN_WORKFLOW_TYPE = "pregnancy_plan"
_PREGNANCY_PLAN_COMMANDS = frozenset(
    {
        "submit_form",
        "answer_current",
        "edit_answer",
        "pause",
        "resume",
        "abandon",
        "generate_plan",
    }
)


def normalize_workflow_command(value: Any) -> dict[str, Any]:
    """Return the bounded client command understood by the deterministic runtime path."""

    if not isinstance(value, dict):
        return {}
    schema_version = _text(value.get("schema_version"), max_length=80)
    workflow_type = _text(value.get("workflow_type"), max_length=120)
    command = _text(value.get("command"), max_length=80)
    if (
        schema_version != PREGNANCY_PLAN_COMMAND_SCHEMA_VERSION
        or workflow_type != PREGNANCY_PLAN_WORKFLOW_TYPE
        or command not in _PREGNANCY_PLAN_COMMANDS
    ):
        return {}

    normalized: dict[str, Any] = {
        "schema_version": schema_version,
        "workflow_type": workflow_type,
        "command": command,
    }
    for key, max_length in (
        ("step_id", 120),
        ("choice_id", 120),
        ("answer", 2000),
        ("scope", 32),
    ):
        text = _text(value.get(key), max_length=max_length)
        if text:
            normalized[key] = text
    if value.get("restart") is True:
        normalized["restart"] = True
    return normalized


def _text(value: Any, *, max_length: int) -> str:
    return value.strip()[:max_length] if isinstance(value, str) else ""
