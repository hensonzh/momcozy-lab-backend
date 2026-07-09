import asyncio
import json
from dataclasses import dataclass
from uuid import uuid4

from production_backend.app.modules.agent_runtime.models import AgentEvent
from production_backend.app.modules.agent_runtime.router import _stream_run_event_chunks
from production_backend.app.modules.agent_runtime.event_stream.ag_ui import AgUiSseEncoder
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


def test_agent_transient_stream_round_trips_run_progress() -> None:
    redis = FakeStreamRedis()
    thread_id = uuid4()
    run_id = uuid4()

    async def exercise():
        stream = AgentTransientStream(redis)
        published = await stream.publish_progress(thread_id=thread_id, run_id=run_id, phase="model_reasoning", label="我想一下")
        events = await stream.read(run_id=run_id, after_cursor="0-0")
        return published, events

    published, events = asyncio.run(exercise())

    assert published is not None
    assert published.event_id == "progress:1-0"
    assert published.type == "run.progress"
    assert published.payload == {"phase": "model_reasoning", "label": "我想一下"}
    assert events == [published]
    encoded = encode_transient_sse_events(events)
    assert "event: run.progress" in encoded
    assert '"transient":true' in encoded
    assert '"phase":"model_reasoning"' in encoded


def test_agent_transient_stream_round_trips_optimistic_application_event() -> None:
    redis = FakeStreamRedis()
    thread_id = uuid4()
    run_id = uuid4()
    tool_call_id = str(uuid4())

    async def exercise():
        stream = AgentTransientStream(redis)
        published = await stream.publish_application_event(
            thread_id=thread_id,
            run_id=run_id,
            event_type="tool.started",
            payload={"tool_call_id": tool_call_id, "tool_name": "records.milk_status.read"},
            dedupe_key=f"{run_id}:tool.started:{tool_call_id}",
        )
        events = await stream.read(run_id=run_id, after_cursor="0-0")
        return published, events

    published, events = asyncio.run(exercise())

    assert published is not None
    assert published.type == "tool.started"
    assert published.payload["tool_call_id"] == tool_call_id
    assert published.payload["_live_semantic"] == {
        "dedupe_key": f"{run_id}:tool.started:{tool_call_id}",
        "durable": False,
        "optimistic": True,
    }
    assert events == [published]


def test_ag_ui_encoder_maps_transient_delta_to_legacy_text_events() -> None:
    thread_id = uuid4()
    run_id = uuid4()
    message_id = str(uuid4())
    event = AgentTransientStreamEvent(
        event_id="delta:1-0",
        type="message.delta",
        thread_id=thread_id,
        run_id=run_id,
        cursor="1-0",
        payload={"delta": "hello", "message_stream_id": message_id},
        created_at="2026-07-04T00:00:00+00:00",
    )

    encoded = AgUiSseEncoder().encode_transient([event])
    payloads = _sse_payloads(encoded)

    assert [payload["type"] for payload in payloads] == ["TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT"]
    assert payloads[0]["message_id"] == message_id
    assert payloads[1]["delta"] == "hello"
    assert payloads[1]["semantic"]["phase"] == "replying"


def test_ag_ui_encoder_attaches_quick_replies_after_final_assistant_message() -> None:
    thread_id = uuid4()
    run_id = uuid4()
    message_id = str(uuid4())
    final_message = AgentEvent(
        event_id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        sequence=2,
        event_type="message.completed",
        payload={
            "message_id": message_id,
            "role": "assistant",
            "text": "完整回复",
            "quick_replies": [{"text": "看今日安排"}, {"text": "先不保存"}, {"text": "换简单版"}],
        },
    )
    completed = AgentEvent(
        event_id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        sequence=3,
        event_type="run.completed",
        payload={},
    )

    payloads = _sse_payloads(AgUiSseEncoder().encode_persisted([final_message, completed]))

    assert [payload["type"] for payload in payloads] == [
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "QUICK_REPLIES",
        "RUN_FINISHED",
    ]
    assert payloads[3]["message_id"] == message_id
    assert payloads[3]["replies"][0]["text"] == "看今日安排"


def test_ag_ui_encoder_maps_new_tools_to_legacy_semantics() -> None:
    thread_id = uuid4()
    run_id = uuid4()
    tool_call_id = str(uuid4())
    started = AgentEvent(
        event_id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        sequence=1,
        event_type="tool.started",
        payload={
            "tool_call_id": tool_call_id,
            "tool_name": "records.milk_status.read",
            "safe_args": {"days": 1},
        },
    )
    completed = AgentEvent(
        event_id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        sequence=2,
        event_type="tool.completed",
        payload={
            "tool_call_id": tool_call_id,
            "tool_name": "records.milk_status.read",
            "safe_output": {"status": "ok"},
        },
    )

    payloads = _sse_payloads(AgUiSseEncoder().encode_persisted([started, completed]))

    assert [payload["type"] for payload in payloads] == [
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_END",
        "TOOL_CALL_RESULT",
    ]
    assert {payload["tool_call_name"] for payload in payloads} == {"milk_status_query"}
    assert payloads[0]["semantic"]["label"] == "我先看看今天的奶量状态～"
    assert payloads[-1]["semantic"]["label"] == "我看好今天的奶量状态啦"


def test_ag_ui_encoder_dedupes_optimistic_tool_event_against_persisted_event() -> None:
    thread_id = uuid4()
    run_id = uuid4()
    tool_call_id = str(uuid4())
    transient_started = AgentTransientStreamEvent(
        event_id="transient:1-0",
        type="tool.started",
        thread_id=thread_id,
        run_id=run_id,
        cursor="1-0",
        payload={
            "tool_call_id": tool_call_id,
            "tool_name": "records.milk_status.read",
            "_live_semantic": {
                "dedupe_key": f"{run_id}:tool.started:{tool_call_id}",
                "optimistic": True,
                "durable": False,
            },
        },
        created_at="2026-07-04T00:00:00+00:00",
    )
    persisted_started = AgentEvent(
        event_id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        sequence=1,
        event_type="tool.started",
        payload={"tool_call_id": tool_call_id, "tool_name": "records.milk_status.read"},
    )
    completed = AgentEvent(
        event_id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        sequence=2,
        event_type="run.completed",
        payload={},
    )
    encoder = AgUiSseEncoder()

    transient_payloads = _sse_payloads(encoder.encode_transient([transient_started]))
    persisted_payloads = _sse_payloads(encoder.encode_persisted([persisted_started]))

    assert transient_payloads[0]["type"] == "TOOL_CALL_START"
    assert transient_payloads[0]["optimistic"] is True
    assert transient_payloads[0]["durable"] is False
    assert transient_payloads[0]["dedupe_key"] == f"{run_id}:tool.started:{tool_call_id}"
    assert persisted_payloads == []


def test_ag_ui_encoder_maps_waiting_run_to_confirmation_required() -> None:
    thread_id = uuid4()
    run_id = uuid4()
    action_id = str(uuid4())
    waiting = AgentEvent(
        event_id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        sequence=4,
        event_type="run.waiting_for_confirmation",
        payload={"action_id": action_id},
    )

    payloads = _sse_payloads(AgUiSseEncoder().encode_persisted([waiting]))

    assert payloads[0]["type"] == "CONFIRMATION_REQUIRED"
    assert payloads[0]["confirmation_id"] == action_id
    assert payloads[0]["semantic"]["phase"] == "confirming"


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

    payloads = _sse_payloads(chunk)
    assert [payload["type"] for payload in payloads] == ["TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT"]
    assert payloads[1]["delta"] == "hello"


def test_stream_run_event_chunks_flushes_transient_delta_before_final_events() -> None:
    thread_id = uuid4()
    run_id = uuid4()
    progress = AgentEvent(
        event_id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        sequence=1,
        event_type="run.progress",
        payload={"label": "正在整理回复"},
    )
    final_message = AgentEvent(
        event_id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        sequence=2,
        event_type="message.completed",
        payload={"message_id": "assistant", "role": "assistant", "text": "完整回复"},
    )
    completed = AgentEvent(
        event_id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        sequence=3,
        event_type="run.completed",
        payload={},
    )
    transient_event = AgentTransientStreamEvent(
        event_id="delta:1-0",
        type="message.delta",
        thread_id=thread_id,
        run_id=run_id,
        cursor="1-0",
        payload={"delta": "流式片段", "message_stream_id": "assistant"},
        created_at="2026-07-04T00:00:00+00:00",
    )

    async def exercise() -> list[str]:
        chunks = []
        async for chunk in _stream_run_event_chunks(
            service=FakeAgentRuntimeService([[progress, final_message, completed]]),
            owner_user_id=uuid4(),
            run_id=run_id,
            after_sequence=0,
            limit=20,
            follow=True,
            poll_interval_seconds=0.1,
            max_wait_seconds=1,
            transient_stream=FakeTransientStream([transient_event]),
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(exercise())

    assert len(chunks) == 3
    assert _sse_payloads(chunks[0])[0]["type"] == "CUSTOM"
    assert [payload["type"] for payload in _sse_payloads(chunks[1])] == ["TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT"]
    assert _sse_payloads(chunks[1])[1]["delta"] == "流式片段"
    assert [payload["type"] for payload in _sse_payloads(chunks[2])] == ["TEXT_MESSAGE_END", "RUN_FINISHED"]


def test_stream_run_event_chunks_dedupes_optimistic_tool_event_before_db_event() -> None:
    thread_id = uuid4()
    run_id = uuid4()
    tool_call_id = str(uuid4())
    transient_started = AgentTransientStreamEvent(
        event_id="transient:1-0",
        type="tool.started",
        thread_id=thread_id,
        run_id=run_id,
        cursor="1-0",
        payload={
            "tool_call_id": tool_call_id,
            "tool_name": "records.milk_status.read",
            "_live_semantic": {
                "dedupe_key": f"{run_id}:tool.started:{tool_call_id}",
                "optimistic": True,
                "durable": False,
            },
        },
        created_at="2026-07-04T00:00:00+00:00",
    )
    persisted_started = AgentEvent(
        event_id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        sequence=1,
        event_type="tool.started",
        payload={"tool_call_id": tool_call_id, "tool_name": "records.milk_status.read"},
    )
    completed = AgentEvent(
        event_id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        sequence=2,
        event_type="run.completed",
        payload={},
    )

    async def exercise() -> list[str]:
        chunks = []
        async for chunk in _stream_run_event_chunks(
            service=FakeAgentRuntimeService([[], [persisted_started, completed]]),
            owner_user_id=uuid4(),
            run_id=run_id,
            after_sequence=0,
            limit=20,
            follow=True,
            poll_interval_seconds=0.1,
            max_wait_seconds=1,
            transient_stream=FakeTransientStream([transient_started]),
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(exercise())

    assert len(chunks) == 2
    payloads = _sse_payloads(chunks[0])
    assert payloads[0]["type"] == "TOOL_CALL_START"
    assert payloads[0]["optimistic"] is True
    assert _sse_payloads(chunks[1])[0]["type"] == "RUN_FINISHED"


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
    def __init__(self, batches=None) -> None:
        self.batches = list(batches or [])

    async def list_events(self, **_kwargs):
        if self.batches:
            return self.batches.pop(0)
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


def _sse_payloads(chunk: str) -> list[dict]:
    payloads = []
    for line in chunk.splitlines():
        if line.startswith("data: "):
            payloads.append(json.loads(line.removeprefix("data: ")))
    return payloads
