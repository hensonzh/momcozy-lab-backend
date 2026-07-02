from __future__ import annotations

import json
from collections.abc import Iterable

from .schemas import AgentEventRead


def encode_sse_events(events: Iterable[object]) -> str:
    return "".join(_encode_event(event) for event in events)


def _encode_event(event: object) -> str:
    read = AgentEventRead.model_validate(event)
    payload = read.model_dump(mode="json", by_alias=True)
    event_type = str(payload.get("type") or "message")
    sequence = str(payload.get("sequence") or "")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {sequence}\nevent: {event_type}\ndata: {data}\n\n"
