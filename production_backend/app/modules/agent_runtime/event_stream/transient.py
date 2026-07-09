from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast
from uuid import UUID

from redis.asyncio import Redis


TRANSIENT_STREAM_MAXLEN = 2000
TRANSIENT_STREAM_TTL_SECONDS = 600


@dataclass(frozen=True)
class AgentTransientStreamEvent:
    event_id: str
    type: str
    thread_id: UUID
    run_id: UUID
    cursor: str
    payload: dict[str, Any]
    created_at: str
    transient: bool = True


class AgentTransientStream:
    def __init__(self, redis_client: Redis) -> None:
        self.redis = redis_client

    async def publish_message_delta(
        self,
        *,
        thread_id: UUID,
        run_id: UUID,
        delta: str,
        message_stream_id: str = "assistant",
        ttl_seconds: int = TRANSIENT_STREAM_TTL_SECONDS,
    ) -> AgentTransientStreamEvent | None:
        normalized_delta = str(delta or "")
        if not normalized_delta:
            return None
        key = _run_transient_stream_key(run_id)
        fields = {
            "type": "message.delta",
            "thread_id": str(thread_id),
            "run_id": str(run_id),
            "payload": json.dumps(
                {
                    "delta": normalized_delta,
                    "message_stream_id": message_stream_id,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            "created_at": _utcnow_iso(),
        }
        cursor = str(
            await self.redis.xadd(
                key,
                cast(dict[Any, Any], fields),
                maxlen=TRANSIENT_STREAM_MAXLEN,
                approximate=True,
            )
        )
        await self.redis.expire(key, ttl_seconds)
        return _event_from_fields(cursor=cursor, fields=fields)

    async def publish_progress(
        self,
        *,
        thread_id: UUID,
        run_id: UUID,
        phase: str,
        label: str,
        ttl_seconds: int = TRANSIENT_STREAM_TTL_SECONDS,
    ) -> AgentTransientStreamEvent | None:
        normalized_phase = str(phase or "")
        normalized_label = str(label or "")
        if not normalized_phase and not normalized_label:
            return None
        key = _run_transient_stream_key(run_id)
        fields = {
            "type": "run.progress",
            "thread_id": str(thread_id),
            "run_id": str(run_id),
            "payload": json.dumps(
                {
                    "phase": normalized_phase,
                    "label": normalized_label,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            "created_at": _utcnow_iso(),
        }
        cursor = str(
            await self.redis.xadd(
                key,
                cast(dict[Any, Any], fields),
                maxlen=TRANSIENT_STREAM_MAXLEN,
                approximate=True,
            )
        )
        await self.redis.expire(key, ttl_seconds)
        return _event_from_fields(cursor=cursor, fields=fields)

    async def publish_application_event(
        self,
        *,
        thread_id: UUID,
        run_id: UUID,
        event_type: str,
        payload: dict[str, Any],
        dedupe_key: str,
        optimistic: bool = True,
        durable: bool = False,
        ttl_seconds: int = TRANSIENT_STREAM_TTL_SECONDS,
    ) -> AgentTransientStreamEvent | None:
        normalized_event_type = str(event_type or "").strip()
        normalized_dedupe_key = str(dedupe_key or "").strip()
        if not normalized_event_type or not normalized_dedupe_key:
            return None
        key = _run_transient_stream_key(run_id)
        live_payload = dict(payload)
        live_payload["_live_semantic"] = {
            "dedupe_key": normalized_dedupe_key,
            "optimistic": bool(optimistic),
            "durable": bool(durable),
        }
        fields = {
            "type": normalized_event_type,
            "thread_id": str(thread_id),
            "run_id": str(run_id),
            "payload": json.dumps(
                live_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            "created_at": _utcnow_iso(),
        }
        cursor = str(
            await self.redis.xadd(
                key,
                cast(dict[Any, Any], fields),
                maxlen=TRANSIENT_STREAM_MAXLEN,
                approximate=True,
            )
        )
        await self.redis.expire(key, ttl_seconds)
        return _event_from_fields(cursor=cursor, fields=fields)

    async def read(
        self,
        *,
        run_id: UUID,
        after_cursor: str = "0-0",
        count: int = 100,
        block_ms: int = 0,
    ) -> list[AgentTransientStreamEvent]:
        response = await self.redis.xread(
            {_run_transient_stream_key(run_id): after_cursor},
            count=count,
            block=block_ms,
        )
        events: list[AgentTransientStreamEvent] = []
        for _stream_key, stream_events in response or []:
            for cursor, fields in stream_events:
                if isinstance(fields, dict):
                    events.append(_event_from_fields(cursor=str(cursor), fields=cast(dict[str, Any], fields)))
        return events


def _event_from_fields(*, cursor: str, fields: dict[str, Any]) -> AgentTransientStreamEvent:
    payload = _json_object(fields.get("payload"))
    event_type = str(fields.get("type") or "message.delta")
    return AgentTransientStreamEvent(
        event_id=f"{_event_id_prefix(event_type)}:{cursor}",
        type=event_type,
        thread_id=UUID(str(fields.get("thread_id"))),
        run_id=UUID(str(fields.get("run_id"))),
        cursor=cursor,
        payload=payload,
        created_at=str(fields.get("created_at") or ""),
    )


def _json_object(raw: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _run_transient_stream_key(run_id: UUID) -> str:
    return f"agent:run:{run_id}:transient_stream"


def _event_id_prefix(event_type: str) -> str:
    if event_type == "message.delta":
        return "delta"
    if event_type == "run.progress":
        return "progress"
    return "transient"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
