import asyncio
from uuid import uuid4

from production_backend.app.modules.agent_runtime.event_stream.publisher import AgentEventPublisher
from production_backend.app.modules.agent_runtime.event_stream.sink import AgentEventSink
from production_backend.app.modules.agent_runtime.models import AgentEvent


def test_agent_event_sink_appends_event_and_updates_stream_cursor() -> None:
    repository = FakeEventRepository()
    controls = FakeControls()
    commits = []
    run_id = uuid4()

    async def after_append() -> None:
        commits.append("commit")

    event = asyncio.run(
        AgentEventSink(repository=repository, controls=controls, after_append=after_append).append_event(
            thread_id=uuid4(),
            run_id=run_id,
            event_type="tool.completed",
            payload={"tool_name": "profile.read"},
        )
    )

    assert event.sequence == 1
    assert repository.events == [event]
    assert controls.stream_cursor == (run_id, 1)
    assert commits == ["commit"]


def test_agent_event_publisher_persists_before_durable_live_publish() -> None:
    repository = FakeEventRepository()
    controls = FakeControls(operations=repository.operations)
    transient_stream = FakeLiveTransientStream(operations=repository.operations)
    run_id = uuid4()
    message_id = str(uuid4())

    async def after_append() -> None:
        repository.operations.append("commit")

    async def exercise() -> None:
        publisher = AgentEventPublisher(
            repository=repository,
            controls=controls,
            after_append=after_append,
            transient_stream=transient_stream,
        )
        await publisher.append_event(
            thread_id=uuid4(),
            run_id=run_id,
            event_type="message.completed",
            payload={"message_id": message_id, "role": "assistant", "text": "Done"},
        )
        await publisher.publish_application_event(
            thread_id=uuid4(),
            run_id=run_id,
            event_type="message.completed",
            payload={"message_id": message_id, "role": "assistant", "text": "Done"},
            dedupe_key=f"{run_id}:message.completed:{message_id}",
            optimistic=False,
            durable=True,
        )

    asyncio.run(exercise())

    assert repository.operations == ["db:message.completed", "cursor:1", "commit", "live:message.completed"]
    assert transient_stream.events[0]["dedupe_key"] == f"{run_id}:message.completed:{message_id}"
    assert transient_stream.events[0]["optimistic"] is False
    assert transient_stream.events[0]["durable"] is True


def test_agent_event_publisher_progress_and_delta_are_transient_only() -> None:
    repository = FakeEventRepository()
    transient_stream = FakeLiveTransientStream(operations=repository.operations)
    run_id = uuid4()
    thread_id = uuid4()

    async def exercise() -> None:
        publisher = AgentEventPublisher(repository=repository, transient_stream=transient_stream)
        await publisher.publish_progress(
            thread_id=thread_id,
            run_id=run_id,
            phase="model_reasoning",
            label="我想一下",
            dedupe_key=f"{run_id}:run.progress:progress:model_reasoning",
        )
        await publisher.publish_message_delta(
            thread_id=thread_id,
            run_id=run_id,
            delta="hello",
            message_stream_id="assistant",
        )

    asyncio.run(exercise())

    assert repository.events == []
    assert repository.operations == ["live:run.progress", "live:message.delta"]
    assert transient_stream.progresses[0]["phase"] == "model_reasoning"
    assert transient_stream.progresses[0]["optimistic"] is True
    assert transient_stream.deltas[0]["delta"] == "hello"


class FakeEventRepository:
    def __init__(self) -> None:
        self.events = []
        self.operations = []

    async def append_event(self, **kwargs):
        self.operations.append(f"db:{kwargs['event_type']}")
        event = AgentEvent(
            event_id=uuid4(),
            thread_id=kwargs["thread_id"],
            run_id=kwargs["run_id"],
            sequence=len(self.events) + 1,
            event_type=kwargs["event_type"],
            payload=kwargs["payload"],
        )
        self.events.append(event)
        return event


class FakeControls:
    def __init__(self, *, operations: list[str] | None = None) -> None:
        self.stream_cursor = None
        self.operations = operations

    async def set_stream_cursor(self, *, run_id, sequence):
        self.stream_cursor = (run_id, sequence)
        if self.operations is not None:
            self.operations.append(f"cursor:{sequence}")


class FakeLiveTransientStream:
    def __init__(self, *, operations: list[str]) -> None:
        self.operations = operations
        self.events = []
        self.progresses = []
        self.deltas = []

    async def publish_application_event(self, **kwargs):
        self.operations.append(f"live:{kwargs['event_type']}")
        self.events.append(kwargs)

    async def publish_progress(self, **kwargs):
        self.operations.append("live:run.progress")
        self.progresses.append(kwargs)

    async def publish_message_delta(self, **kwargs):
        self.operations.append("live:message.delta")
        self.deltas.append(kwargs)
