from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from app.infrastructure.object_storage.base import ObjectStorage


DEFAULT_MAX_INLINE_PAYLOAD_BYTES = 32 * 1024
AGENT_PAYLOAD_CONTENT_TYPE = "application/json"


@dataclass(frozen=True)
class ExternalizedPayload:
    inline_payload: dict[str, Any]
    raw_payload_ref: str


async def maybe_externalize_json_payload(
    *,
    payload: dict[str, Any],
    object_storage: ObjectStorage | None,
    run_id: UUID,
    payload_kind: str,
    max_inline_bytes: int = DEFAULT_MAX_INLINE_PAYLOAD_BYTES,
    key_suffix: str | None = None,
) -> ExternalizedPayload:
    body = _json_bytes(payload)
    if object_storage is None or len(body) <= max_inline_bytes:
        return ExternalizedPayload(inline_payload=payload, raw_payload_ref="")

    safe_suffix = _safe_key_part(key_suffix or uuid4().hex)
    key = f"agent-runtime/runs/{run_id}/{_safe_key_part(payload_kind)}/{safe_suffix}.json"
    stored = await object_storage.put_bytes(key=key, body=body, content_type=AGENT_PAYLOAD_CONTENT_TYPE)
    return ExternalizedPayload(
        inline_payload={
            "payload_summary": bounded_payload(payload),
            "_externalized_payload": {
                "stored": True,
                "size_bytes": stored.size_bytes,
                "content_type": stored.content_type,
            },
        },
        raw_payload_ref=stored.uri,
    )


def bounded_payload(
    value: Any,
    *,
    max_depth: int = 6,
    max_items: int = 20,
    max_string_chars: int = 1000,
) -> Any:
    if max_depth <= 0:
        return "[truncated]"
    if isinstance(value, dict):
        items = list(value.items())
        bounded_dict = {
            str(key): bounded_payload(
                item,
                max_depth=max_depth - 1,
                max_items=max_items,
                max_string_chars=max_string_chars,
            )
            for key, item in items[:max_items]
        }
        if len(items) > max_items:
            bounded_dict["_truncated_key_count"] = len(items) - max_items
        return bounded_dict
    if isinstance(value, list):
        bounded_list = [
            bounded_payload(
                item,
                max_depth=max_depth - 1,
                max_items=max_items,
                max_string_chars=max_string_chars,
            )
            for item in value[:max_items]
        ]
        if len(value) > max_items:
            bounded_list.append({"_truncated_item_count": len(value) - max_items})
        return bounded_list
    if isinstance(value, str) and len(value) > max_string_chars:
        return f"{value[:max_string_chars]}...[truncated {len(value) - max_string_chars} chars]"
    return value


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _safe_key_part(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value.strip()) or uuid4().hex
