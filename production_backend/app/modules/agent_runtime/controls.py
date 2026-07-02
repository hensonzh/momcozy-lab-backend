from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import UUID, uuid4


class AgentRunControls:
    def __init__(self, redis_client) -> None:
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

    async def acquire_run_lock(self, *, run_id: UUID, owner_token: str, ttl_seconds: int = 60) -> bool:
        return bool(await self.redis.set(_run_lock_key(run_id), owner_token, ex=ttl_seconds, nx=True))

    async def release_run_lock(self, *, run_id: UUID, owner_token: str) -> None:
        key = _run_lock_key(run_id)
        current = await self.redis.get(key)
        if current and str(current) == owner_token:
            await self.redis.delete(key)

    @asynccontextmanager
    async def run_lock(self, *, run_id: UUID, ttl_seconds: int = 60) -> AsyncIterator[bool]:
        owner_token = uuid4().hex
        acquired = await self.acquire_run_lock(run_id=run_id, owner_token=owner_token, ttl_seconds=ttl_seconds)
        try:
            yield acquired
        finally:
            if acquired:
                await self.release_run_lock(run_id=run_id, owner_token=owner_token)


def _run_lock_key(run_id: UUID) -> str:
    return f"agent:run:{run_id}:lock"


def _cancel_key(run_id: UUID) -> str:
    return f"agent:run:{run_id}:cancel"


def _stream_cursor_key(run_id: UUID) -> str:
    return f"agent:run:{run_id}:stream_cursor"


def _active_run_key(thread_id: UUID) -> str:
    return f"agent:thread:{thread_id}:active_run"
