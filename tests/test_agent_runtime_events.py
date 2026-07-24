import asyncio
from uuid import uuid4

from app.agent_runtime.events.publisher import AgentEventPublisher
from app.agent_runtime.runs.models import AgentEvent


def test_agent_event_sink_appends_event_and_updates_stream_cursor() -> None:
    repository = FakeEventRepository()
    controls = FakeControls()
    commits = []
    run_id = uuid4()

    async def after_append() -> None:
        commits.append("commit")

    event = asyncio.run(
        AgentEventPublisher(repository=repository, controls=controls, after_append=after_append).append_event(
            thread_id=uuid4(),
            run_id=run_id,
            event_type="tool.completed",
            payload={"tool_name": "profile_read"},
        )
    )

    assert event.sequence == 1
    assert repository.events == [event]
    assert controls.stream_cursor == (run_id, 1)
    assert commits == ["commit"]


def test_agent_event_sink_batches_related_events_into_one_commit() -> None:
    repository = FakeEventRepository()
    controls = FakeControls(operations=repository.operations)
    run_id = uuid4()

    async def after_append() -> None:
        repository.operations.append("commit")

    events = asyncio.run(
        AgentEventPublisher(repository=repository, controls=controls, after_append=after_append).append_events(
            thread_id=uuid4(),
            run_id=run_id,
            events=(
                ("tool.completed", {"tool_name": "pregnancy_diary_read"}),
                ("pregnancy_diary.changed", {"operation": "created"}),
            ),
        )
    )

    assert [event.event_type for event in events] == ["tool.completed", "pregnancy_diary.changed"]
    assert repository.operations == [
        "db:tool.completed",
        "db:pregnancy_diary.changed",
        "commit",
        "cursor:2",
    ]
    assert controls.stream_cursor == (run_id, 2)


def test_agent_event_sink_can_stage_batch_inside_caller_savepoint_before_commit() -> None:
    repository = FakeEventRepository()
    controls = FakeControls(operations=repository.operations)
    run_id = uuid4()

    async def after_append() -> None:
        repository.operations.append("commit")

    async def exercise() -> None:
        sink = AgentEventPublisher(repository=repository, controls=controls, after_append=after_append)
        events = await sink.stage_events(
            thread_id=uuid4(),
            run_id=run_id,
            events=(
                ("tool.completed", {"tool_name": "pregnancy_diary_read"}),
                ("pregnancy_diary.changed", {"operation": "created"}),
            ),
        )
        assert repository.operations == [
            "db:tool.completed",
            "db:pregnancy_diary.changed",
        ]
        assert controls.stream_cursor is None
        await sink.finalize_staged_events(run_id=run_id, events=events)

    asyncio.run(exercise())

    assert repository.operations == [
        "db:tool.completed",
        "db:pregnancy_diary.changed",
        "commit",
        "cursor:2",
    ]


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

    assert repository.operations == ["db:message.completed", "commit", "cursor:1", "live:message.completed"]
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


def test_agent_event_publisher_defers_redis_state_until_outer_commit() -> None:
    repository = FakeAfterCommitEventRepository()
    controls = FakeControls(operations=repository.operations)
    publisher = AgentEventPublisher(repository=repository, controls=controls)
    run_id = uuid4()
    thread_id = uuid4()

    async def exercise() -> None:
        await publisher.append_event(
            thread_id=thread_id,
            run_id=run_id,
            event_type="pregnancy_diary.changed",
            payload={"operation": "deleted"},
        )
        await publisher.clear_active_run(thread_id=thread_id, run_id=run_id)
        assert controls.stream_cursor is None
        assert controls.cleared_active_run is None
        await repository.run_after_commit_callbacks()

    asyncio.run(exercise())

    assert repository.operations == [
        "db:pregnancy_diary.changed",
        "cursor:1",
        "clear_active_run",
    ]
    assert controls.stream_cursor == (run_id, 1)
    assert controls.cleared_active_run == (thread_id, run_id)


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


class FakeAfterCommitEventRepository(FakeEventRepository):
    def __init__(self) -> None:
        super().__init__()
        self.after_commit_callbacks = []

    def add_after_commit_callback(self, callback) -> None:
        self.after_commit_callbacks.append(callback)

    async def run_after_commit_callbacks(self) -> None:
        callbacks = list(self.after_commit_callbacks)
        self.after_commit_callbacks.clear()
        for callback in callbacks:
            await callback()


class FakeControls:
    def __init__(self, *, operations: list[str] | None = None) -> None:
        self.stream_cursor = None
        self.cleared_active_run = None
        self.operations = operations

    async def set_stream_cursor(self, *, run_id, sequence):
        self.stream_cursor = (run_id, sequence)
        if self.operations is not None:
            self.operations.append(f"cursor:{sequence}")

    async def clear_active_run(self, *, thread_id, run_id):
        self.cleared_active_run = (thread_id, run_id)
        if self.operations is not None:
            self.operations.append("clear_active_run")


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
