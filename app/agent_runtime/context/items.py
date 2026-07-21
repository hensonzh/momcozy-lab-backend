from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContextItemAppend:
    item_key: str
    item: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.item_key.strip():
            raise ValueError("ContextItemAppend.item_key must not be empty.")
        if not self.item:
            raise ValueError("ContextItemAppend.item must not be empty.")

    @property
    def item_type(self) -> str:
        item_type = self.item.get("type")
        if isinstance(item_type, str) and item_type:
            return item_type
        if isinstance(self.item.get("role"), str):
            return "message"
        return "unknown"


def message_context_item(*, role: str, content: dict[str, Any]) -> dict[str, Any]:
    normalized_role = role if role in {"user", "assistant"} else "user"
    text = content.get("text")
    message_text = text if isinstance(text, str) else ""
    if normalized_role != "user":
        return {"role": normalized_role, "content": message_text}

    image_inputs = _image_inputs(content.get("attachments"))
    if not image_inputs:
        return {"role": normalized_role, "content": message_text}
    return {
        "role": normalized_role,
        "content": [
            {"type": "input_text", "text": message_text},
            *image_inputs,
        ],
    }


def _image_inputs(attachments: Any) -> list[dict[str, str]]:
    if not isinstance(attachments, list):
        return []
    inputs: list[dict[str, str]] = []
    for attachment in attachments:
        if not isinstance(attachment, dict) or attachment.get("type") != "image":
            continue
        asset_id = attachment.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id.strip():
            continue
        raw_detail = attachment.get("detail")
        detail = raw_detail if raw_detail in {"auto", "low", "high"} else "auto"
        inputs.append({"type": "input_image", "asset_id": asset_id, "detail": detail})
    return inputs
