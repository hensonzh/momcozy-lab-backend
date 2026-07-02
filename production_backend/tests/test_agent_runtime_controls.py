import asyncio
from uuid import uuid4

from production_backend.app.modules.agent_runtime.controls import AgentRunControls


def test_agent_run_controls_manage_active_run_cancel_and_cursor() -> None:
    redis = FakeRedis()
    controls = AgentRunControls(redis)
    thread_id = uuid4()
    run_id = uuid4()

    asyncio.run(controls.set_active_run(thread_id=thread_id, run_id=run_id))
    asyncio.run(controls.request_cancel(run_id=run_id))
    asyncio.run(controls.set_stream_cursor(run_id=run_id, sequence=7))

    assert asyncio.run(controls.get_active_run(thread_id=thread_id)) == str(run_id)
    assert asyncio.run(controls.is_cancel_requested(run_id=run_id)) is True
    assert asyncio.run(controls.get_stream_cursor(run_id=run_id)) == 7

    asyncio.run(controls.clear_active_run(thread_id=thread_id, run_id=uuid4()))
    assert asyncio.run(controls.get_active_run(thread_id=thread_id)) == str(run_id)

    asyncio.run(controls.clear_active_run(thread_id=thread_id, run_id=run_id))
    asyncio.run(controls.clear_cancel(run_id=run_id))
    asyncio.run(controls.clear_stream_cursor(run_id=run_id))

    assert asyncio.run(controls.get_active_run(thread_id=thread_id)) is None
    assert asyncio.run(controls.is_cancel_requested(run_id=run_id)) is False
    assert asyncio.run(controls.get_stream_cursor(run_id=run_id)) is None


def test_agent_run_controls_lock_context_releases_owned_lock() -> None:
    redis = FakeRedis()
    controls = AgentRunControls(redis)
    run_id = uuid4()

    async def exercise() -> tuple[bool, bool, str | None]:
        async with controls.run_lock(run_id=run_id) as first:
            second = await controls.acquire_run_lock(run_id=run_id, owner_token="other")
        released = await redis.get(f"agent:run:{run_id}:lock")
        return first, second, released

    first, second, released = asyncio.run(exercise())

    assert first is True
    assert second is False
    assert released is None


class FakeRedis:
    def __init__(self) -> None:
        self.values = {}

    async def set(self, key, value, *, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = str(value)
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        self.values.pop(key, None)
        return 1
