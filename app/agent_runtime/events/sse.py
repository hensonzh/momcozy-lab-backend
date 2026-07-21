from __future__ import annotations

import json
from collections.abc import Iterable

from app.agent_runtime.api.schemas import AgentEventRead

from .transient import AgentTransientStreamEvent


def encode_sse_events(events: Iterable[object]) -> str:
    return "".join(_encode_event(event) for event in events)


def encode_transient_sse_events(events: Iterable[AgentTransientStreamEvent]) -> str:
    return "".join(_encode_transient_event(event) for event in events)


def _encode_event(event: object) -> str:
    read = AgentEventRead.model_validate(event)
    payload = read.model_dump(mode="json", by_alias=True)
    event_type = str(payload.get("type") or "message")
    sequence = str(payload.get("sequence") or "")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {sequence}\nevent: {event_type}\ndata: {data}\n\n"


def _encode_transient_event(event: AgentTransientStreamEvent) -> str:
    payload = {
        "event_id": event.event_id,
        "type": event.type,
        "thread_id": str(event.thread_id),
        "run_id": str(event.run_id),
        "transient": True,
        "cursor": event.cursor,
        "payload": event.payload,
        "created_at": event.created_at,
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return f"id: {event.event_id}\nevent: {event.type}\ndata: {data}\n\n"
