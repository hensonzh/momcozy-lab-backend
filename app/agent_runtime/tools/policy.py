from __future__ import annotations

from typing import Any, cast

from app.agent_runtime.events.semantics import with_tool_event_semantic


class ToolExecutionPolicy:
    """Provider-neutral argument safety and event presentation hooks."""

    def safe_args(self, *, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        del tool_name
        return cast(dict[str, Any], _safe_payload(args))

    def event_output_summary(self, *, tool_name: str, output: dict[str, Any]) -> dict[str, Any]:
        """Return the small presentation state needed by tool lifecycle events."""
        del tool_name
        return {
            key: output[key]
            for key in (
                "status",
                "operation",
                "plan_type",
                "requires_confirmation",
                "action_status",
                "write_succeeded",
                "artifact_type",
            )
            if key in output
        }

    def effective_effect_scope(self, *, tool_name: str, args: dict[str, Any], default: str) -> str:
        del tool_name, args
        return default

    def event_label(self, *, tool_name: str, payload: dict[str, Any] | None = None) -> str:
        del tool_name, payload
        return "相关信息"

    def enrich_event(
        self,
        payload: dict[str, Any],
        *,
        event_type: str,
        tool_name: str,
        output: dict[str, Any] | None = None,
        effect_scope: str = "none",
    ) -> dict[str, Any]:
        return with_tool_event_semantic(
            payload,
            event_type=event_type,
            tool_name=tool_name,
            output=output,
            effect_scope=effect_scope,
        )


def _safe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redacted_value(key, item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_payload(item) for item in value]
    return value


def _redacted_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if lowered in {"confirmed_form_data", "form_data"}:
        return "[redacted]"
    if any(token in lowered for token in ("authorization", "password", "secret", "token", "api_key")):
        return "[redacted]"
    return _safe_payload(value)
