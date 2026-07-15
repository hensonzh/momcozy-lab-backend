from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, cast
from uuid import UUID, uuid4

from redis.asyncio import Redis


_EXTEND_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], tonumber(ARGV[2]))
end
return 0
"""
_RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""


class AgentRunControls:
    def __init__(self, redis_client: Redis) -> None:
        self.redis = redis_client

    async def set_active_run(self, *, thread_id: UUID, run_id: UUID, ttl_seconds: int = 3600) -> None:
        await self.redis.set(_active_run_key(thread_id), str(run_id), ex=ttl_seconds)

    async def get_active_run(self, *, thread_id: UUID) -> str | None:
        value = await self.redis.get(_active_run_key(thread_id))
        return str(value) if value else None

    async def clear_active_run(self, *, thread_id: UUID, run_id: UUID | None = None) -> None:
        key = _active_run_key(thread_id)
        if run_id is not None:
            current = await self.redis.get(key)
            if current and str(current) != str(run_id):
                return
        await self.redis.delete(key)

    async def request_cancel(self, *, run_id: UUID, ttl_seconds: int = 86400) -> None:
        await self.redis.set(_cancel_key(run_id), "1", ex=ttl_seconds)

    async def is_cancel_requested(self, *, run_id: UUID) -> bool:
        return bool(await self.redis.get(_cancel_key(run_id)))

    async def clear_cancel(self, *, run_id: UUID) -> None:
        await self.redis.delete(_cancel_key(run_id))

    async def set_stream_cursor(self, *, run_id: UUID, sequence: int, ttl_seconds: int = 86400) -> None:
        await self.redis.set(_stream_cursor_key(run_id), str(sequence), ex=ttl_seconds)

    async def get_stream_cursor(self, *, run_id: UUID) -> int | None:
        value = await self.redis.get(_stream_cursor_key(run_id))
        return int(value) if value is not None else None

    async def clear_stream_cursor(self, *, run_id: UUID) -> None:
        await self.redis.delete(_stream_cursor_key(run_id))

    async def notify_run_queued(self, *, run_id: UUID, ttl_seconds: int = 3600) -> None:
        key = _run_queue_wakeup_key()
        await cast(Awaitable[Any], self.redis.lpush(key, str(run_id)))
        await cast(Awaitable[Any], self.redis.ltrim(key, 0, 999))
        await cast(Awaitable[Any], self.redis.expire(key, ttl_seconds))

    async def wait_for_run_queue_signal(self, *, timeout_seconds: float) -> str | None:
        if timeout_seconds <= 0:
            return None
        result = await cast(Awaitable[Any], self.redis.blpop(_run_queue_wakeup_key(), timeout=timeout_seconds))
        if not result:
            return None
        _key, value = result
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    async def notify_fact_queued(self, *, run_id: UUID, ttl_seconds: int = 3600) -> None:
        key = _fact_queue_wakeup_key()
        await cast(Awaitable[Any], self.redis.lpush(key, str(run_id)))
        await cast(Awaitable[Any], self.redis.ltrim(key, 0, 999))
        await cast(Awaitable[Any], self.redis.expire(key, ttl_seconds))

    async def wait_for_fact_queue_signal(self, *, timeout_seconds: float) -> str | None:
        if timeout_seconds <= 0:
            return None
        result = await cast(Awaitable[Any], self.redis.blpop(_fact_queue_wakeup_key(), timeout=timeout_seconds))
        if not result:
            return None
        _key, value = result
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    async def acquire_run_lock(self, *, run_id: UUID, owner_token: str, ttl_seconds: int = 60) -> bool:
        return bool(await self.redis.set(_run_lock_key(run_id), owner_token, ex=ttl_seconds, nx=True))

    async def extend_run_lock(self, *, run_id: UUID, owner_token: str, ttl_seconds: int = 60) -> bool:
        key = _run_lock_key(run_id)
        result = await cast(Awaitable[Any], self.redis.eval(_EXTEND_LOCK_SCRIPT, 1, key, owner_token, str(ttl_seconds)))
        return bool(result)

    async def release_run_lock(self, *, run_id: UUID, owner_token: str) -> None:
        key = _run_lock_key(run_id)
        await cast(Awaitable[Any], self.redis.eval(_RELEASE_LOCK_SCRIPT, 1, key, owner_token))

    @asynccontextmanager
    async def run_lock(
        self,
        *,
        run_id: UUID,
        ttl_seconds: int = 60,
        refresh_interval_seconds: float | None = None,
    ) -> AsyncIterator[bool]:
        owner_token = uuid4().hex
        acquired = await self.acquire_run_lock(run_id=run_id, owner_token=owner_token, ttl_seconds=ttl_seconds)
        refresh_task: asyncio.Task[None] | None = None
        if acquired:
            refresh_task = asyncio.create_task(
                self._refresh_run_lock(
                    run_id=run_id,
                    owner_token=owner_token,
                    ttl_seconds=ttl_seconds,
                    refresh_interval_seconds=refresh_interval_seconds or _default_refresh_interval(ttl_seconds),
                )
            )
        try:
            yield acquired
        finally:
            if refresh_task is not None:
                refresh_task.cancel()
                try:
                    await refresh_task
                except asyncio.CancelledError:
                    pass
            if acquired:
                await self.release_run_lock(run_id=run_id, owner_token=owner_token)

    async def _refresh_run_lock(
        self,
        *,
        run_id: UUID,
        owner_token: str,
        ttl_seconds: int,
        refresh_interval_seconds: float,
    ) -> None:
        while True:
            await asyncio.sleep(refresh_interval_seconds)
            renewed = await self.extend_run_lock(run_id=run_id, owner_token=owner_token, ttl_seconds=ttl_seconds)
            if not renewed:
                return


def _run_lock_key(run_id: UUID) -> str:
    return f"agent:run:{run_id}:lock"


def _cancel_key(run_id: UUID) -> str:
    return f"agent:run:{run_id}:cancel"


def _stream_cursor_key(run_id: UUID) -> str:
    return f"agent:run:{run_id}:stream_cursor"


def _run_queue_wakeup_key() -> str:
    return "agent:run_queue:wakeup"


def _fact_queue_wakeup_key() -> str:
    return "agent:fact_queue:wakeup"


def _active_run_key(thread_id: UUID) -> str:
    return f"agent:thread:{thread_id}:active_run"


def _default_refresh_interval(ttl_seconds: int) -> float:
    return max(min(ttl_seconds / 3, 30), 1)
