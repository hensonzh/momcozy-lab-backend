from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.modules.files.agent_asset_capability import (
    AgentAssetCapability,
    AgentAssetCapabilityStore,
    AgentAssetCapabilityUnavailable,
)


NOW = datetime(2026, 7, 28, tzinfo=timezone.utc)


def test_authorized_issue_reuses_one_opaque_token_and_slides_both_redis_ttls() -> None:
    redis = FakeRedis()
    clock = MutableClock(NOW)
    store = AgentAssetCapabilityStore(
        redis_client=redis,
        inactivity_ttl_seconds=1800,
        clock=clock,
        token_factory=lambda: "a" * 43,
    )
    capability = _capability()

    first = asyncio.run(store.issue_or_refresh(capability))
    clock.value = NOW + timedelta(minutes=20)
    second = asyncio.run(store.issue_or_refresh(capability))

    assert first.token == second.token == "a" * 43
    assert first.expires_at == NOW + timedelta(minutes=30)
    assert second.expires_at == NOW + timedelta(minutes=50)
    assert len(redis.values) == 2
    assert sorted(redis.ttls.values()) == [1800, 1800]
    assert redis.expire_calls[-2:] == [
        (store.logical_key_for(capability), 1800),
        (store.token_key_for(first.token), 1800),
    ]


def test_external_capability_lookup_does_not_extend_inactivity_ttl() -> None:
    redis = FakeRedis()
    store = AgentAssetCapabilityStore(
        redis_client=redis,
        inactivity_ttl_seconds=1800,
        clock=lambda: NOW,
        token_factory=lambda: "b" * 43,
    )
    capability = _capability()
    issued = asyncio.run(store.issue_or_refresh(capability))
    expire_call_count = len(redis.expire_calls)

    resolved = asyncio.run(store.get(issued.token))

    assert resolved == capability
    assert len(redis.expire_calls) == expire_call_count


def test_capability_token_is_random_bearer_data_not_a_deterministic_file_identifier() -> None:
    owner_user_id = uuid4()
    file_id = uuid4()
    capability = _capability(owner_user_id=owner_user_id, file_id=file_id)
    store = AgentAssetCapabilityStore(
        redis_client=FakeRedis(),
        inactivity_ttl_seconds=1800,
        clock=lambda: NOW,
        token_factory=lambda: "c" * 43,
    )

    issued = asyncio.run(store.issue_or_refresh(capability))

    assert str(owner_user_id) not in issued.token
    assert str(file_id) not in issued.token
    assert capability.object_key not in issued.token
    assert issued.token not in store.token_key_for(issued.token)


def test_invalidate_revokes_image_and_file_capabilities_for_the_object_version() -> None:
    redis = FakeRedis()
    tokens = iter(("d" * 43, "e" * 43))
    store = AgentAssetCapabilityStore(
        redis_client=redis,
        inactivity_ttl_seconds=1800,
        clock=lambda: NOW,
        token_factory=lambda: next(tokens),
    )
    image_capability = _capability(purpose="model_image", content_type="image/png")
    file_capability = AgentAssetCapability(
        owner_user_id=image_capability.owner_user_id,
        file_id=image_capability.file_id,
        object_key=image_capability.object_key,
        purpose="model_file",
        content_type="application/pdf",
    )
    image = asyncio.run(store.issue_or_refresh(image_capability))
    document = asyncio.run(store.issue_or_refresh(file_capability))

    asyncio.run(
        store.invalidate(
            owner_user_id=image_capability.owner_user_id,
            file_id=image_capability.file_id,
            object_key=image_capability.object_key,
        )
    )

    assert asyncio.run(store.get(image.token)) is None
    assert asyncio.run(store.get(document.token)) is None
    assert redis.values == {}


def test_capability_mapping_changes_with_the_immutable_object_version() -> None:
    store = AgentAssetCapabilityStore(
        redis_client=FakeRedis(),
        inactivity_ttl_seconds=1800,
        clock=lambda: NOW,
    )
    first = _capability(object_key="users/u/files/v1/photo.png")
    replacement = _capability(
        owner_user_id=first.owner_user_id,
        file_id=first.file_id,
        object_key="users/u/files/v2/photo.png",
    )

    assert store.logical_key_for(first) != store.logical_key_for(replacement)


def test_concurrent_authorized_resolves_converge_on_one_capability() -> None:
    redis = FakeRedis(yield_between_operations=True)
    candidates = iter(f"{index:043d}" for index in range(1, 51))
    store = AgentAssetCapabilityStore(
        redis_client=redis,
        inactivity_ttl_seconds=1800,
        clock=lambda: NOW,
        token_factory=lambda: next(candidates),
    )
    capability = _capability()

    async def issue_all() -> list[object]:
        return list(
            await asyncio.gather(
                *(
                    store.issue_or_refresh(capability)
                    for _index in range(20)
                )
            )
        )

    issued = asyncio.run(issue_all())

    assert len({item.token for item in issued}) == 1  # type: ignore[attr-defined]
    assert len(redis.values) == 2


def test_expired_token_record_is_not_reused_on_the_next_authorized_resolve() -> None:
    redis = FakeRedis()
    candidates = iter(("f" * 43, "g" * 43))
    store = AgentAssetCapabilityStore(
        redis_client=redis,
        inactivity_ttl_seconds=1800,
        clock=lambda: NOW,
        token_factory=lambda: next(candidates),
    )
    capability = _capability()
    first = asyncio.run(store.issue_or_refresh(capability))
    redis.values.pop(store.token_key_for(first.token))

    second = asyncio.run(store.issue_or_refresh(capability))

    assert second.token == "g" * 43
    assert asyncio.run(store.get(first.token)) is None


def test_capability_issue_fails_closed_when_redis_is_unavailable() -> None:
    store = AgentAssetCapabilityStore(
        redis_client=FailingRedis(),
        inactivity_ttl_seconds=1800,
        clock=lambda: NOW,
    )

    with pytest.raises(AgentAssetCapabilityUnavailable):
        asyncio.run(store.issue_or_refresh(_capability()))


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FakeRedis:
    def __init__(
        self,
        *,
        yield_between_operations: bool = False,
    ) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.expire_calls: list[tuple[str, int]] = []
        self.yield_between_operations = yield_between_operations

    async def get(self, key: str) -> str | None:
        await self._yield()
        return self.values.get(key)

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int,
        nx: bool,
    ) -> bool | None:
        await self._yield()
        if nx and key in self.values:
            return None
        self.values[key] = value
        self.ttls[key] = ex
        return True

    async def expire(self, key: str, seconds: int) -> bool:
        await self._yield()
        self.expire_calls.append((key, seconds))
        if key not in self.values:
            return False
        self.ttls[key] = seconds
        return True

    async def delete(self, *keys: str) -> int:
        await self._yield()
        deleted = 0
        for key in keys:
            if key in self.values:
                del self.values[key]
                self.ttls.pop(key, None)
                deleted += 1
        return deleted

    async def _yield(self) -> None:
        if self.yield_between_operations:
            await asyncio.sleep(0)


class FailingRedis:
    async def get(self, _key: str) -> str | None:
        raise RuntimeError("redis unavailable")


def _capability(
    *,
    owner_user_id: object | None = None,
    file_id: object | None = None,
    object_key: str = "users/u/files/v1/photo.png",
    purpose: str = "model_image",
    content_type: str = "image/png",
) -> AgentAssetCapability:
    return AgentAssetCapability(
        owner_user_id=owner_user_id or uuid4(),  # type: ignore[arg-type]
        file_id=file_id or uuid4(),  # type: ignore[arg-type]
        object_key=object_key,
        purpose=purpose,  # type: ignore[arg-type]
        content_type=content_type,
    )
