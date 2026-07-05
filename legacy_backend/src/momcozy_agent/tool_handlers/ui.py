from __future__ import annotations

from typing import Any

from ..types import RuntimeInputs


MAX_QUICK_REPLY_TEXT_CHARS = 32


def create_quick_replies(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    guided_replies = _normalized_replies(inputs.get("_quick_reply_guidance"), allow_string_items=True)
    if guided_replies is not None:
        normalized = guided_replies
    else:
        replies = args.get("replies")
        if not isinstance(replies, list):
            raise ValueError("replies must be a list of exactly 3 items.")
        normalized = _normalized_replies(replies)
        if normalized is None:
            raise ValueError("quick replies require exactly 3 unique non-empty items.")

    return {
        "status": "quick_replies_ready",
        "quick_replies": normalized,
        "side_effect_performed": False,
    }


def _normalized_replies(value: Any, *, allow_string_items: bool = False) -> list[dict[str, str]] | None:
    if not isinstance(value, list):
        return None

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, dict):
            raw_text = item.get("text")
        elif allow_string_items:
            raw_text = item
        else:
            continue
        text = _trim_text(raw_text)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"text": text})

    if len(normalized) != 3:
        return None
    return normalized


def _trim_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = " ".join(text.split())
    return text[:MAX_QUICK_REPLY_TEXT_CHARS]
