from __future__ import annotations

from typing import Any


INSTRUCTIONAL_TOOL_OUTPUT_KEYS = frozenset(
    {
        "assistant_hint",
        "assistant_instruction",
        "developer_prompt",
        "instruction",
        "instructions",
        "model_instruction",
        "next_step",
        "next_step_hint",
        "prompt",
        "reply_instruction",
        "system_prompt",
    }
)


def strip_instructional_tool_output_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: strip_instructional_tool_output_keys(item)
            for key, item in value.items()
            if _normalized_key(key) not in INSTRUCTIONAL_TOOL_OUTPUT_KEYS
        }
    if isinstance(value, list):
        return [strip_instructional_tool_output_keys(item) for item in value]
    return value


def _normalized_key(value: object) -> str:
    return str(value).strip().lower().replace("-", "_")
