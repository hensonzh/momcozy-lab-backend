from __future__ import annotations

import json
import re
from hashlib import sha256
from dataclasses import dataclass, field
from typing import Any


THINK_TAG = "<think>"
APPEND_ONLY_TEXT_STREAM_SCHEMA_VERSION = "append-only.v1"


@dataclass(frozen=True)
class SanitizedAgentResponseText:
    text: str
    quick_replies: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class AgentResponseTextIntegrity:
    utf8_bytes: int
    sha256: str


def agent_response_text_integrity(text: str) -> AgentResponseTextIntegrity:
    encoded = str(text or "").encode("utf-8")
    return AgentResponseTextIntegrity(utf8_bytes=len(encoded), sha256=sha256(encoded).hexdigest())


class AppendOnlyAgentResponseProjector:
    def __init__(self) -> None:
        self._raw_text = ""
        self.text = ""
        self._finalized = False

    def push(self, delta: str) -> str:
        if self._finalized:
            raise RuntimeError("Cannot append text after the response projector is finalized.")
        self._raw_text += str(delta or "")
        stable_end = _stable_response_prefix_end(self._raw_text)
        return self._commit(sanitize_agent_response_text(self._raw_text[:stable_end]).text)

    def finalize(self) -> str:
        if self._finalized:
            return ""
        self._finalized = True
        stable_end = _stable_response_prefix_end(self._raw_text)
        return self._commit(sanitize_agent_response_text(self._raw_text[:stable_end]).text)

    def _commit(self, candidate: str) -> str:
        if not candidate.startswith(self.text):
            return ""
        delta = candidate[len(self.text) :]
        self.text = candidate
        return delta


def sanitize_agent_response_text(text: str) -> SanitizedAgentResponseText:
    normalized = _strip_think_text(str(text or ""))
    if _looks_like_partial_structured_json(normalized):
        return SanitizedAgentResponseText(text="")
    without_fences = _replace_structured_json_fences(normalized)
    without_chunks = _replace_structured_json_chunks(without_fences)
    cleaned = _clean_response_text(without_chunks)
    return SanitizedAgentResponseText(text=cleaned)


def _stable_response_prefix_end(text: str) -> int:
    unstable_starts = [
        start
        for start in (
            _unclosed_json_start(text),
            _unclosed_fence_start(text),
            _possible_quick_reply_header_start(text),
            _trailing_whitespace_start(text),
        )
        if start is not None
    ]
    return min(unstable_starts, default=len(text))


def _unclosed_json_start(text: str) -> int | None:
    stack: list[tuple[str, int]] = []
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if not stack:
            if char in "{[":
                stack.append(("}" if char == "{" else "]", index))
            continue
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
            stack.append(("}" if char == "{" else "]", index))
        elif char == stack[-1][0]:
            stack.pop()
    return stack[0][1] if stack else None


def _unclosed_fence_start(text: str) -> int | None:
    search_from = 0
    while True:
        start = text.find("```", search_from)
        if start < 0:
            break
        end = text.find("```", start + 3)
        if end < 0:
            return start
        search_from = end + 3
    for marker_size in (2, 1):
        marker = "`" * marker_size
        if text.endswith(marker):
            return len(text) - marker_size
    return None


def _possible_quick_reply_header_start(text: str) -> int | None:
    line_start = max(text.rfind("\n"), text.rfind("\r")) + 1
    line = text[line_start:].lstrip()
    if not line:
        return None
    normalized = line.lower()
    headers = ("快捷回复", "推荐回复", "quick replies", "quick_replies", "replies")
    for header in headers:
        if header.startswith(normalized):
            return line_start
        if normalized.startswith(header) and normalized[len(header) :].strip(" \t:：") == "":
            return line_start
    return None


def _trailing_whitespace_start(text: str) -> int | None:
    stripped = text.rstrip()
    return len(stripped) if len(stripped) < len(text) else None


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
        return (
            len(structured_items) == len(value)
            and bool(structured_items)
            and all(_looks_like_tool_or_runtime_json(item) for item in structured_items)
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
