from __future__ import annotations

from typing import Any

from ..types import RuntimeInputs


MAX_QUICK_REPLY_TEXT_CHARS = 32


def create_quick_replies(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    replies = args.get("replies")
    if not isinstance(replies, list):
        raise ValueError("replies must be a list of exactly 3 items.")

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in replies:
        if not isinstance(item, dict):
            continue
        text = _trim_text(item.get("text"))
        send_text = _trim_text(item.get("send_text")) or text
        if not text or not send_text:
            continue
        key = send_text.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"text": text, "send_text": send_text})

    if len(normalized) != 3:
        raise ValueError("quick replies require exactly 3 unique non-empty items.")

    return {
        "status": "quick_replies_ready",
        "quick_replies": normalized,
        "side_effect_performed": False,
    }


def _trim_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = " ".join(text.split())
    return text[:MAX_QUICK_REPLY_TEXT_CHARS]
