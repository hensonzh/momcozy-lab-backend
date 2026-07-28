from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.modules.files.agent_url_cache import AgentFileUrlCache, CachedAgentFileUrl


NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


def test_agent_file_url_cache_keeps_the_first_published_url_for_the_reuse_window() -> None:
    redis = FakeRedis()
    cache = AgentFileUrlCache(
        redis_client=redis,
        reuse_ttl_seconds=1800,
        minimum_remaining_seconds=300,
        clock=lambda: NOW,
    )
    owner_user_id = uuid4()
    file_id = uuid4()
    first = CachedAgentFileUrl(
        model_url="https://assets.example.test/signed/first",
        expires_at=NOW + timedelta(hours=1),
    )
    racing = CachedAgentFileUrl(
        model_url="https://assets.example.test/signed/racing",
        expires_at=NOW + timedelta(hours=1),
    )

    selected_first = asyncio.run(
        cache.publish_or_get(
            owner_user_id=owner_user_id,
            file_id=file_id,
            purpose="model_image",
            object_key="users/u/files/v1/photo.png",
            candidate=first,
        )
    )
    selected_racing = asyncio.run(
        cache.publish_or_get(
            owner_user_id=owner_user_id,
            file_id=file_id,
            purpose="model_image",
            object_key="users/u/files/v1/photo.png",
            candidate=racing,
        )
    )

    assert selected_first == first
    assert selected_racing == first
    assert redis.expirations == [1800]
    assert all(call["nx"] is True for call in redis.set_calls)


def test_agent_file_url_cache_key_changes_with_the_immutable_object_version() -> None:
    cache = AgentFileUrlCache(
        redis_client=FakeRedis(),
        reuse_ttl_seconds=1800,
        minimum_remaining_seconds=300,
        clock=lambda: NOW,
    )
    owner_user_id = uuid4()
    file_id = uuid4()

    first = cache.key_for(
        owner_user_id=owner_user_id,
        file_id=file_id,
        purpose="model_image",
        object_key="users/u/files/v1/photo.png",
    )
    replacement = cache.key_for(
        owner_user_id=owner_user_id,
        file_id=file_id,
        purpose="model_image",
        object_key="users/u/files/v2/photo.png",
    )

    assert first != replacement


def test_agent_file_url_cache_rejects_a_cached_url_without_fetch_safety_window() -> None:
    redis = FakeRedis()
    cache = AgentFileUrlCache(
        redis_client=redis,
        reuse_ttl_seconds=1800,
        minimum_remaining_seconds=300,
        clock=lambda: NOW,
    )
    owner_user_id = uuid4()
    file_id = uuid4()
    key = cache.key_for(
        owner_user_id=owner_user_id,
        file_id=file_id,
        purpose="model_file",
        object_key="users/u/files/v1/report.pdf",
    )
    redis.values[key] = json.dumps(
        {
            "version": 1,
            "model_url": "https://assets.example.test/signed/report",
            "expires_at": (NOW + timedelta(seconds=299)).isoformat(),
        }
    )

    result = asyncio.run(
        cache.get(
            owner_user_id=owner_user_id,
            file_id=file_id,
            purpose="model_file",
            object_key="users/u/files/v1/report.pdf",
        )
    )

    assert result is None
    assert key not in redis.values


def test_agent_file_url_cache_rejects_an_unsafe_cached_url() -> None:
    redis = FakeRedis()
    cache = AgentFileUrlCache(
        redis_client=redis,
        reuse_ttl_seconds=1800,
        minimum_remaining_seconds=300,
        clock=lambda: NOW,
    )
    owner_user_id = uuid4()
    file_id = uuid4()
    key = cache.key_for(
        owner_user_id=owner_user_id,
        file_id=file_id,
        purpose="model_image",
        object_key="users/u/files/v1/photo.png",
    )
    redis.values[key] = json.dumps(
        {
            "version": 1,
            "model_url": "http://storage.internal/photo.png",
            "expires_at": (NOW + timedelta(hours=1)).isoformat(),
        }
    )

    result = asyncio.run(
        cache.get(
            owner_user_id=owner_user_id,
            file_id=file_id,
            purpose="model_image",
            object_key="users/u/files/v1/photo.png",
        )
    )

    assert result is None
    assert key not in redis.values


def test_agent_file_url_cache_fails_open_when_redis_is_unavailable() -> None:
    cache = AgentFileUrlCache(
        redis_client=FailingRedis(),
        reuse_ttl_seconds=1800,
        minimum_remaining_seconds=300,
        clock=lambda: NOW,
    )
    owner_user_id = uuid4()
    file_id = uuid4()
    candidate = CachedAgentFileUrl(
        model_url="https://assets.example.test/signed/report",
        expires_at=NOW + timedelta(hours=1),
    )

    assert (
        asyncio.run(
            cache.get(
                owner_user_id=owner_user_id,
                file_id=file_id,
                purpose="model_file",
                object_key="users/u/files/v1/report.pdf",
            )
        )
        is None
    )
    assert (
        asyncio.run(
            cache.publish_or_get(
                owner_user_id=owner_user_id,
                file_id=file_id,
                purpose="model_file",
                object_key="users/u/files/v1/report.pdf",
                candidate=candidate,
            )
        )
        == candidate
    )
    asyncio.run(
        cache.invalidate(
            owner_user_id=owner_user_id,
            file_id=file_id,
            object_key="users/u/files/v1/report.pdf",
        )
    )


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[dict[str, object]] = []
        self.expirations: list[int] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool | None:
        self.set_calls.append({"key": key, "value": value, "ex": ex, "nx": nx})
        if nx and key in self.values:
            return None
        self.values[key] = value
        self.expirations.append(ex)
        return True

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for key in keys:
            if key in self.values:
                del self.values[key]
                deleted += 1
        return deleted


class FailingRedis:
    async def get(self, _key: str) -> str | None:
        raise RuntimeError("redis unavailable")

    async def set(self, _key: str, _value: str, *, ex: int, nx: bool) -> bool | None:
        raise RuntimeError(f"redis unavailable: ex={ex}, nx={nx}")

    async def delete(self, *_keys: str) -> int:
        raise RuntimeError("redis unavailable")
