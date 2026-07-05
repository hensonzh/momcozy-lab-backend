import asyncio
from dataclasses import dataclass
from uuid import uuid4

from production_backend.app.modules.agent_runtime.models import AgentEvent
from production_backend.app.modules.agent_runtime.router import _stream_run_event_chunks
from production_backend.app.modules.agent_runtime.event_stream.sse import encode_sse_events, encode_transient_sse_events
from production_backend.app.modules.agent_runtime.event_stream.transient import AgentTransientStream, AgentTransientStreamEvent


def test_encode_sse_events_uses_application_event_envelope() -> None:
    thread_id = uuid4()
    run_id = uuid4()
    event = AgentEvent(
        event_id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        sequence=3,
        event_type="message.completed",
        payload={"message_id": "msg_1"},
    )

    encoded = encode_sse_events([event])

    assert "id: 3" in encoded
    assert "event: message.completed" in encoded
    assert '"thread_id":"' in encoded
    assert '"run_id":"' in encoded
    assert '"type":"message.completed"' in encoded
    assert "provider" not in encoded


def test_encode_transient_sse_events_uses_delta_cursor_not_db_sequence() -> None:
    thread_id = uuid4()
    run_id = uuid4()
    event = AgentTransientStreamEvent(
        event_id="delta:1-0",
        type="message.delta",
        thread_id=thread_id,
        run_id=run_id,
        cursor="1-0",
        payload={"delta": "hel", "message_stream_id": "assistant"},
        created_at="2026-07-04T00:00:00+00:00",
    )

    encoded = encode_transient_sse_events([event])

    assert "id: delta:1-0" in encoded
    assert "event: message.delta" in encoded
    assert '"transient":true' in encoded
    assert '"sequence"' not in encoded
    assert '"delta":"hel"' in encoded


def test_agent_transient_stream_round_trips_message_delta() -> None:
    redis = FakeStreamRedis()
    thread_id = uuid4()
    run_id = uuid4()

    async def exercise():
        stream = AgentTransientStream(redis)
        published = await stream.publish_message_delta(thread_id=thread_id, run_id=run_id, delta="hello")
        events = await stream.read(run_id=run_id, after_cursor="0-0")
        return published, events

    published, events = asyncio.run(exercise())

    assert published is not None
    assert published.type == "message.delta"
    assert published.payload["delta"] == "hello"
    assert events == [published]
    assert redis.expired_keys == {f"agent:run:{run_id}:transient_stream"}


def test_stream_run_event_chunks_yields_transient_delta_while_following() -> None:
    thread_id = uuid4()
    run_id = uuid4()
    transient_event = AgentTransientStreamEvent(
        event_id="delta:1-0",
        type="message.delta",
        thread_id=thread_id,
        run_id=run_id,
        cursor="1-0",
        payload={"delta": "hello", "message_stream_id": "assistant"},
        created_at="2026-07-04T00:00:00+00:00",
    )

    async def exercise() -> str:
        chunks = _stream_run_event_chunks(
            service=FakeAgentRuntimeService(),
            owner_user_id=uuid4(),
            run_id=run_id,
            after_sequence=0,
            limit=20,
            follow=True,
            poll_interval_seconds=0.1,
            max_wait_seconds=1,
            transient_stream=FakeTransientStream([transient_event]),
        )
        return await anext(chunks)

    chunk = asyncio.run(exercise())

    assert "event: message.delta" in chunk
    assert '"delta":"hello"' in chunk


class FakeStreamRedis:
    def __init__(self) -> None:
        self.streams = {}
        self.expired_keys = set()
        self.next_id = 1

    async def xadd(self, key, fields, *, maxlen=None, approximate=True):
        cursor = f"{self.next_id}-0"
        self.next_id += 1
        self.streams.setdefault(key, []).append((cursor, fields))
        return cursor

    async def expire(self, key, ttl_seconds):
        self.expired_keys.add(key)
        return True

    async def xread(self, streams, *, count=None, block=None):
        result = []
        for key, after_cursor in streams.items():
            events = [
                (cursor, fields)
                for cursor, fields in self.streams.get(key, [])
                if _cursor_index(cursor) > _cursor_index(after_cursor)
            ]
            if count is not None:
                events = events[:count]
            if events:
                result.append((key, events))
        return result


class FakeAgentRuntimeService:
    async def list_events(self, **_kwargs):
        return []


@dataclass
class FakeTransientStream:
    batches: list[list[AgentTransientStreamEvent] | AgentTransientStreamEvent]

    async def read(self, **_kwargs):
        if not self.batches:
            return []
        batch = self.batches.pop(0)
        return batch if isinstance(batch, list) else [batch]


def _cursor_index(cursor: str) -> int:
    return int(str(cursor).split("-", maxsplit=1)[0])
