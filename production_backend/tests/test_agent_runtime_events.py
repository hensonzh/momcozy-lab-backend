import asyncio
from uuid import uuid4

from production_backend.app.modules.agent_runtime.events import AgentEventSink
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


class FakeEventRepository:
    def __init__(self) -> None:
        self.events = []

    async def append_event(self, **kwargs):
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
    def __init__(self) -> None:
        self.stream_cursor = None

    async def set_stream_cursor(self, *, run_id, sequence):
        self.stream_cursor = (run_id, sequence)
