from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


THINK_TAG = "<think>"


@dataclass(frozen=True)
class SanitizedAgentResponseText:
    text: str
    quick_replies: list[dict[str, Any]] = field(default_factory=list)


def sanitize_agent_response_text(text: str) -> SanitizedAgentResponseText:
    normalized = _strip_think_text(str(text or ""))
    if _looks_like_partial_structured_json(normalized):
        return SanitizedAgentResponseText(text="")
    without_fences = _replace_structured_json_fences(normalized)
    without_chunks = _replace_structured_json_chunks(without_fences)
    cleaned = _clean_response_text(without_chunks)
    return SanitizedAgentResponseText(text=cleaned)


def _strip_think_text(text: str) -> str:
    without_closed_blocks = re.sub(r"(?is)<think>.*?</think>\s*", "", text)
    without_open_block = re.sub(r"(?is)<think>.*$", "", without_closed_blocks)
    lower_text = without_open_block.lower()
    max_partial_len = min(len(THINK_TAG) - 1, len(lower_text))
    for size in range(max_partial_len, 0, -1):
        if THINK_TAG.startswith(lower_text[-size:]):
            return without_open_block[:-size].strip()
    return without_open_block.strip()


def _replace_structured_json_fences(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        raw_json = match.group("json")
        parsed = _json_value(raw_json)
        if parsed is None:
            return match.group(0)
        replacement, should_replace = _structured_replacement(parsed)
        if not should_replace:
            return match.group(0)
        return replacement

    return re.sub(
        r"```(?:json)?\s*(?P<json>[\s\S]*?)\s*```",
        replace,
        text,
        flags=re.IGNORECASE,
    )


def _replace_structured_json_chunks(text: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char in "{[":
            end = _balanced_json_end(text, index)
            if end is not None:
                raw_json = text[index:end]
                parsed = _json_value(raw_json)
                if parsed is not None:
                    replacement, should_replace = _structured_replacement(parsed)
                    if should_replace:
                        if replacement:
                            output.append(replacement)
                        index = end
                        continue
        output.append(char)
        index += 1
    return "".join(output)


def _structured_replacement(value: Any) -> tuple[str, bool]:
    replacement_text = _text_from_structured_value(value)
    if _looks_like_tool_or_runtime_json(value):
        return replacement_text, True
    return "", False


def _text_from_structured_value(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    for key in (
        "text",
        "message",
        "final_text",
        "finalText",
        "assistant_response",
        "assistantResponse",
        "response",
    ):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    message = value.get("message")
    if isinstance(message, dict):
        return _text_from_structured_value(message)
    payload = value.get("payload")
    if isinstance(payload, dict):
        return _text_from_structured_value(payload)
    return ""


def _looks_like_tool_or_runtime_json(value: Any) -> bool:
    if isinstance(value, list):
        structured_items = [item for item in value if isinstance(item, dict | list)]
        return len(structured_items) == len(value) and bool(structured_items) and all(
            _looks_like_tool_or_runtime_json(item) for item in structured_items
        )
    if not isinstance(value, dict):
        return False
    keys = {str(key) for key in value}
    if keys & {
        "tool_call_id",
        "tool_name",
        "safe_args",
        "safe_output",
        "service_skill_id",
        "skill_version",
        "tool_scope",
        "recommended_tools",
        "business_facts",
        "display_name",
        "profile",
        "quick_replies",
        "quickReplies",
        "replies",
    }:
        return True
    status = str(value.get("status") or "")
    if status == "service_skill_loaded" or status.startswith("needs_"):
        return True
    nested = [item for item in value.values() if isinstance(item, dict | list)]
    return bool(nested) and all(_looks_like_tool_or_runtime_json(item) for item in nested)


def _looks_like_partial_structured_json(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped or stripped[0] not in "{[":
        return False
    if _json_value(stripped) is not None:
        return False
    return True


def _json_value(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _balanced_json_end(text: str, start: int) -> int | None:
    opening = text[start]
    closing = "}" if opening == "{" else "]"
    stack = [closing]
    in_string = False
    escaped = False
    for index in range(start + 1, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif stack and char == stack[-1]:
            stack.pop()
            if not stack:
                return index + 1
    return None


def _clean_response_text(text: str) -> str:
    cleaned = re.sub(r"```(?:json)?\s*```", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"(?im)^\s*(快捷回复|推荐回复|quick replies|quick_replies|replies)\s*[:：]\s*$", "", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
