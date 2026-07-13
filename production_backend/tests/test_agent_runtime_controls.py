import asyncio
from uuid import uuid4

from production_backend.app.modules.agent_runtime.run_lifecycle.controls import AgentRunControls


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


def test_agent_run_controls_round_trips_queue_wakeup_signal() -> None:
    redis = FakeRedis()
    controls = AgentRunControls(redis)
    run_id = uuid4()

    async def exercise() -> str | None:
        await controls.notify_run_queued(run_id=run_id)
        return await controls.wait_for_run_queue_signal(timeout_seconds=0.01)

    signal = asyncio.run(exercise())

    assert signal == str(run_id)
    assert redis.expired_keys == {"agent:run_queue:wakeup"}


def test_agent_run_controls_keep_fact_wakeup_separate_from_run_queue() -> None:
    redis = FakeRedis()
    controls = AgentRunControls(redis)
    run_id = uuid4()

    async def exercise() -> tuple[str | None, str | None]:
        await controls.notify_fact_queued(run_id=run_id)
        run_signal = await controls.wait_for_run_queue_signal(timeout_seconds=0)
        fact_signal = await controls.wait_for_fact_queue_signal(timeout_seconds=0.01)
        return run_signal, fact_signal

    run_signal, fact_signal = asyncio.run(exercise())

    assert run_signal is None
    assert fact_signal == str(run_id)
    assert redis.expired_keys == {"agent:fact_queue:wakeup"}


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


def test_agent_run_controls_extend_lock_requires_owner_token() -> None:
    redis = FakeRedis()
    controls = AgentRunControls(redis)
    run_id = uuid4()

    async def exercise() -> tuple[bool, bool]:
        acquired = await controls.acquire_run_lock(run_id=run_id, owner_token="owner")
        other_extended = await controls.extend_run_lock(run_id=run_id, owner_token="other", ttl_seconds=30)
        owner_extended = await controls.extend_run_lock(run_id=run_id, owner_token="owner", ttl_seconds=30)
        return acquired and other_extended, owner_extended

    other_extended, owner_extended = asyncio.run(exercise())

    assert other_extended is False
    assert owner_extended is True


def test_agent_run_controls_lock_context_refreshes_owned_lock() -> None:
    redis = FakeRedis()
    controls = AgentRunControls(redis)
    run_id = uuid4()

    async def exercise() -> int:
        async with controls.run_lock(run_id=run_id, ttl_seconds=30, refresh_interval_seconds=0.01) as acquired:
            assert acquired is True
            await asyncio.sleep(0.03)
        return redis.set_count

    set_count = asyncio.run(exercise())

    assert set_count >= 2


class FakeRedis:
    def __init__(self) -> None:
        self.values = {}
        self.lists = {}
        self.expired_keys = set()
        self.set_count = 0

    async def set(self, key, value, *, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = str(value)
        self.set_count += 1
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        self.values.pop(key, None)
        return 1

    async def lpush(self, key, value):
        values = self.lists.setdefault(key, [])
        values.insert(0, str(value))
        return len(values)

    async def ltrim(self, key, start, end):
        values = self.lists.get(key, [])
        self.lists[key] = values[start : end + 1]
        return True

    async def expire(self, key, ttl_seconds):
        self.expired_keys.add(key)
        return True

    async def blpop(self, key, timeout=0):
        values = self.lists.get(key, [])
        if values:
            return key, values.pop(0)
        if timeout:
            await asyncio.sleep(timeout)
        return None

    async def eval(self, script, numkeys, *keys_and_args):
        key = keys_and_args[0]
        owner_token = keys_and_args[1]
        if "expire" in script:
            if self.values.get(key) == owner_token:
                self.set_count += 1
                return 1
            return 0
        if "del" in script:
            if self.values.get(key) == owner_token:
                await self.delete(key)
                return 1
            return 0
        return 0
