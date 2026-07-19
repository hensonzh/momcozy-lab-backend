from __future__ import annotations

from typing import Any


INSTRUCTIONAL_TOOL_OUTPUT_KEYS = frozenset(
    {
        "assistant_hint",
        "assistant_followup",
        "assistant_guidance",
        "assistant_instruction",
        "assistant_message",
        "continuation_instruction",
        "decision_instruction",
        "developer_prompt",
        "field_guidance",
        "final_reply_instruction",
        "final_response_instruction",
        "followup_policy",
        "instruction",
        "instructions",
        "joint_reasoning_guidance",
        "model_guidance",
        "model_instruction",
        "next_step",
        "next_step_hint",
        "next_question",
        "prompt",
        "prompt_hint",
        "reply_guidance",
        "reply_instruction",
        "response_contract",
        "response_instruction",
        "suggested_questions",
        "suggested_reply",
        "system_prompt",
        "tool_guidance",
        "workflow_control",
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
