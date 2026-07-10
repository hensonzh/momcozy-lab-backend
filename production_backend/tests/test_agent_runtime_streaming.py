import asyncio
import json
import logging
from dataclasses import dataclass
from uuid import uuid4

from production_backend.app.modules.agent_runtime.models import AgentEvent
from production_backend.app.modules.agent_runtime.router import (
    _persisted_fallback_poll_interval_seconds,
    _stream_run_event_chunks,
    _transient_block_ms,
)
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


def test_stream_polling_uses_fast_transient_and_slower_persisted_fallback() -> None:
    assert _transient_block_ms(0.01) == 10
    assert _transient_block_ms(0.05) == 50
    assert _persisted_fallback_poll_interval_seconds(0.01) == 0.1
    assert _persisted_fallback_poll_interval_seconds(0.05) == 0.1
    assert _persisted_fallback_poll_interval_seconds(0.2) == 0.2


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


def test_agent_transient_stream_round_trips_semantic_run_progress() -> None:
    redis = FakeStreamRedis()
    thread_id = uuid4()
    run_id = uuid4()
    semantic = {
        "phase": "replying",
        "label": "我在组织回复～",
        "surface": "status_bar",
        "visibility": "status",
        "merge_key": "progress:response_finalizing",
        "priority": 80,
        "lifecycle": "running",
    }

    async def exercise():
        stream = AgentTransientStream(redis)
        published = await stream.publish_progress(
            thread_id=thread_id,
            run_id=run_id,
            phase="response_finalizing",
            label="我在组织回复～",
            semantic=semantic,
            dedupe_key=f"{run_id}:run.progress:progress:response_finalizing",
        )
        events = await stream.read(run_id=run_id, after_cursor="0-0")
        return published, events

    published, events = asyncio.run(exercise())

    assert published is not None
    assert published.payload["semantic"] == semantic
    assert published.payload["_live_semantic"] == {
        "dedupe_key": f"{run_id}:run.progress:progress:response_finalizing",
        "durable": False,
        "optimistic": True,
    }
    assert events == [published]


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
    assert [payload["type"] for payload in payloads] == ["message.delta"]
    assert payloads[0]["payload"]["delta"] == "hello"


def test_stream_run_event_chunks_logs_stream_timing(caplog) -> None:
    caplog.set_level(logging.INFO, logger="production_backend.agent_runtime")
    thread_id = uuid4()
    run_id = uuid4()
    terminal_event = AgentTransientStreamEvent(
        event_id="run.completed:1-0",
        type="run.completed",
        thread_id=thread_id,
        run_id=run_id,
        cursor="1-0",
        payload={"reason": "ok"},
        created_at="2026-07-04T00:00:00+00:00",
    )

    async def exercise() -> list[str]:
        chunks = []
        async for chunk in _stream_run_event_chunks(
            service=FakeAgentRuntimeService(),
            owner_user_id=uuid4(),
            run_id=run_id,
            after_sequence=0,
            limit=20,
            follow=True,
            poll_interval_seconds=0.1,
            max_wait_seconds=1,
            transient_stream=FakeTransientStream([terminal_event]),
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(exercise())

    assert [payload["type"] for payload in _sse_payloads(chunks[0])] == ["run.completed"]
    payloads = [json.loads(record.getMessage()) for record in caplog.records if record.name == "production_backend.agent_runtime"]
    timing = next(payload for payload in payloads if payload["event"] == "agent.run.sse_stream")
    assert timing["run_id"] == str(run_id)
    assert timing["end_reason"] == "terminal_transient"
    assert timing["chunks_sent"] == 1
    assert timing["events_sent"] == 1
    assert timing["first_event_ms"] >= 0


def test_stream_run_event_chunks_uses_transient_stream_before_next_persisted_poll() -> None:
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
    service = FakeAgentRuntimeService([[]])
    transient_stream = FakeTransientStream([[], transient_event])

    async def exercise() -> str:
        chunks = _stream_run_event_chunks(
            service=service,
            owner_user_id=uuid4(),
            run_id=run_id,
            after_sequence=0,
            limit=20,
            follow=True,
            poll_interval_seconds=0.01,
            max_wait_seconds=1,
            transient_stream=transient_stream,
        )
        return await anext(chunks)

    chunk = asyncio.run(exercise())

    payloads = _sse_payloads(chunk)
    assert [payload["type"] for payload in payloads] == ["message.delta"]
    assert payloads[0]["payload"]["delta"] == "hello"
    assert len(service.calls) == 1
    assert [call["block_ms"] for call in transient_stream.calls] == [10, 10]


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
    assert _sse_payloads(chunks[0])[0]["type"] == "run.progress"
    assert [payload["type"] for payload in _sse_payloads(chunks[1])] == ["message.delta"]
    assert _sse_payloads(chunks[1])[0]["payload"]["delta"] == "流式片段"
    assert [payload["type"] for payload in _sse_payloads(chunks[2])] == ["message.completed", "run.completed"]


def test_stream_run_event_chunks_does_not_treat_user_message_as_final() -> None:
    thread_id = uuid4()
    run_id = uuid4()
    queued = AgentEvent(
        event_id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        sequence=1,
        event_type="run.queued",
        payload={},
    )
    user_message = AgentEvent(
        event_id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        sequence=2,
        event_type="message.completed",
        payload={"message_id": "user", "role": "user"},
    )
    progress = AgentEvent(
        event_id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        sequence=3,
        event_type="run.progress",
        payload={"label": "我想一下"},
    )
    final_message = AgentEvent(
        event_id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        sequence=4,
        event_type="message.completed",
        payload={"message_id": "assistant", "role": "assistant", "text": "完整回复"},
    )
    completed = AgentEvent(
        event_id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        sequence=5,
        event_type="run.completed",
        payload={},
    )

    async def exercise() -> list[str]:
        chunks = []
        async for chunk in _stream_run_event_chunks(
            service=FakeAgentRuntimeService([[queued, user_message, progress, final_message, completed]]),
            owner_user_id=uuid4(),
            run_id=run_id,
            after_sequence=0,
            limit=20,
            follow=True,
            poll_interval_seconds=0.1,
            max_wait_seconds=1,
            transient_stream=FakeTransientStream([]),
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(exercise())

    assert len(chunks) == 2
    assert [payload["type"] for payload in _sse_payloads(chunks[0])] == ["run.queued", "message.completed", "run.progress"]
    assert [payload["type"] for payload in _sse_payloads(chunks[1])] == ["message.completed", "run.completed"]
    assert _sse_payloads(chunks[0])[1]["payload"]["role"] == "user"
    assert _sse_payloads(chunks[1])[0]["payload"]["role"] == "assistant"


def test_stream_run_event_chunks_can_finish_from_transient_terminal_events() -> None:
    thread_id = uuid4()
    run_id = uuid4()
    message_id = str(uuid4())
    transient_message = AgentTransientStreamEvent(
        event_id="transient:1-0",
        type="message.completed",
        thread_id=thread_id,
        run_id=run_id,
        cursor="1-0",
        payload={
            "message_id": message_id,
            "role": "assistant",
            "text": "完整回复",
            "_live_semantic": {
                "dedupe_key": f"{run_id}:message.completed:{message_id}",
                "optimistic": False,
                "durable": True,
            },
        },
        created_at="2026-07-04T00:00:00+00:00",
    )
    transient_completed = AgentTransientStreamEvent(
        event_id="transient:2-0",
        type="run.completed",
        thread_id=thread_id,
        run_id=run_id,
        cursor="2-0",
        payload={
            "_live_semantic": {
                "dedupe_key": f"{run_id}:run.completed",
                "optimistic": False,
                "durable": True,
            },
        },
        created_at="2026-07-04T00:00:01+00:00",
    )

    async def exercise() -> list[str]:
        chunks = []
        async for chunk in _stream_run_event_chunks(
            service=FakeAgentRuntimeService([[]]),
            owner_user_id=uuid4(),
            run_id=run_id,
            after_sequence=0,
            limit=20,
            follow=True,
            poll_interval_seconds=0.1,
            max_wait_seconds=1,
            transient_stream=FakeTransientStream([[transient_message, transient_completed]]),
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(exercise())

    assert len(chunks) == 1
    payloads = _sse_payloads(chunks[0])
    assert [payload["type"] for payload in payloads] == ["message.completed", "run.completed"]
    assert payloads[0]["payload"]["text"] == "完整回复"


def test_stream_run_event_chunks_dedupes_transient_and_persisted_final_events() -> None:
    thread_id = uuid4()
    run_id = uuid4()
    message_id = str(uuid4())
    transient_message = AgentTransientStreamEvent(
        event_id="transient:1-0",
        type="message.completed",
        thread_id=thread_id,
        run_id=run_id,
        cursor="1-0",
        payload={
            "message_id": message_id,
            "role": "assistant",
            "text": "完整回复",
            "_live_semantic": {
                "dedupe_key": f"{run_id}:message.completed:{message_id}",
                "optimistic": False,
                "durable": True,
            },
        },
        created_at="2026-07-04T00:00:00+00:00",
    )
    transient_completed = AgentTransientStreamEvent(
        event_id="transient:2-0",
        type="run.completed",
        thread_id=thread_id,
        run_id=run_id,
        cursor="2-0",
        payload={
            "_live_semantic": {
                "dedupe_key": f"{run_id}:run.completed",
                "optimistic": False,
                "durable": True,
            },
        },
        created_at="2026-07-04T00:00:01+00:00",
    )
    persisted_message = AgentEvent(
        event_id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        sequence=1,
        event_type="message.completed",
        payload={"message_id": message_id, "role": "assistant", "text": "完整回复"},
    )
    persisted_completed = AgentEvent(
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
            service=FakeAgentRuntimeService([[persisted_message, persisted_completed]]),
            owner_user_id=uuid4(),
            run_id=run_id,
            after_sequence=0,
            limit=20,
            follow=True,
            poll_interval_seconds=0.1,
            max_wait_seconds=1,
            transient_stream=FakeTransientStream([[transient_message, transient_completed]]),
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(exercise())

    assert len(chunks) == 1
    payloads = _sse_payloads(chunks[0])
    assert [payload["type"] for payload in payloads] == ["message.completed", "run.completed"]
    assert payloads[0]["transient"] is True


def test_stream_run_event_chunks_keeps_transient_completed_message_quick_replies_before_terminal_event() -> None:
    thread_id = uuid4()
    run_id = uuid4()
    message_id = str(uuid4())
    transient_message = AgentTransientStreamEvent(
        event_id="transient:1-0",
        type="message.completed",
        thread_id=thread_id,
        run_id=run_id,
        cursor="1-0",
        payload={
            "message_id": message_id,
            "role": "assistant",
            "text": "完整回复",
            "quick_replies": [
                {"id": "qr_1", "text": "继续聊这个"},
                {"id": "qr_2", "text": "给我更多细节"},
                {"id": "qr_3", "text": "换个方向"},
            ],
            "_live_semantic": {
                "dedupe_key": f"{run_id}:message.completed:{message_id}",
                "optimistic": False,
                "durable": False,
            },
        },
        created_at="2026-07-04T00:00:00+00:00",
    )
    transient_completed = AgentTransientStreamEvent(
        event_id="transient:2-0",
        type="run.completed",
        thread_id=thread_id,
        run_id=run_id,
        cursor="3-0",
        payload={
            "_live_semantic": {
                "dedupe_key": f"{run_id}:run.completed",
                "optimistic": False,
                "durable": True,
            },
        },
        created_at="2026-07-04T00:00:01+00:00",
    )

    async def exercise() -> list[str]:
        chunks = []
        async for chunk in _stream_run_event_chunks(
            service=FakeAgentRuntimeService([[]]),
            owner_user_id=uuid4(),
            run_id=run_id,
            after_sequence=0,
            limit=20,
            follow=True,
            poll_interval_seconds=0.1,
            max_wait_seconds=1,
            transient_stream=FakeTransientStream([[transient_message, transient_completed]]),
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(exercise())

    payloads = _sse_payloads(chunks[0])
    assert [payload["type"] for payload in payloads] == ["message.completed", "run.completed"]
    assert payloads[0]["payload"]["quick_replies"] == [
        {"id": "qr_1", "text": "继续聊这个"},
        {"id": "qr_2", "text": "给我更多细节"},
        {"id": "qr_3", "text": "换个方向"},
    ]


def test_stream_run_event_chunks_dedupes_optimistic_tool_event_before_persisted_event() -> None:
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
    assert payloads[0]["type"] == "tool.started"
    assert payloads[0]["payload"]["_live_semantic"]["optimistic"] is True
    assert _sse_payloads(chunks[1])[0]["type"] == "run.completed"


def test_stream_run_event_chunks_dedupes_stale_transient_tool_event_after_persisted_event() -> None:
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
            service=FakeAgentRuntimeService([[persisted_started, completed]]),
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
    assert _sse_payloads(chunks[0])[0]["type"] == "tool.started"
    assert _sse_payloads(chunks[1])[0]["type"] == "run.completed"


def test_stream_run_event_chunks_dedupes_semantic_progress_before_persisted_event() -> None:
    thread_id = uuid4()
    run_id = uuid4()
    semantic = {
        "phase": "replying",
        "label": "我在组织回复～",
        "surface": "status_bar",
        "visibility": "status",
        "merge_key": "progress:response_finalizing",
        "priority": 80,
        "lifecycle": "running",
    }
    transient_progress = AgentTransientStreamEvent(
        event_id="progress:1-0",
        type="run.progress",
        thread_id=thread_id,
        run_id=run_id,
        cursor="1-0",
        payload={
            "phase": "response_finalizing",
            "label": "我在组织回复～",
            "semantic": semantic,
            "_live_semantic": {
                "dedupe_key": f"{run_id}:run.progress:progress:response_finalizing",
                "optimistic": True,
                "durable": False,
            },
        },
        created_at="2026-07-04T00:00:00+00:00",
    )
    persisted_progress = AgentEvent(
        event_id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        sequence=1,
        event_type="run.progress",
        payload={
            "phase": "response_finalizing",
            "label": "我在组织回复～",
            "semantic": semantic,
        },
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
            service=FakeAgentRuntimeService([[], [persisted_progress, completed]]),
            owner_user_id=uuid4(),
            run_id=run_id,
            after_sequence=0,
            limit=20,
            follow=True,
            poll_interval_seconds=0.1,
            max_wait_seconds=1,
            transient_stream=FakeTransientStream([transient_progress]),
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(exercise())

    assert len(chunks) == 2
    assert _sse_payloads(chunks[0])[0]["type"] == "run.progress"
    assert _sse_payloads(chunks[1])[0]["type"] == "run.completed"


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
        self.calls = []

    async def list_events(self, **_kwargs):
        self.calls.append(_kwargs)
        if self.batches:
            return self.batches.pop(0)
        return []


@dataclass
class FakeTransientStream:
    batches: list[list[AgentTransientStreamEvent] | AgentTransientStreamEvent]

    def __post_init__(self) -> None:
        self.calls = []

    async def read(self, **_kwargs):
        self.calls.append(_kwargs)
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
