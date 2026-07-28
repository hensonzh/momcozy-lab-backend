from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from app.modules.files.agent_asset_capability import (
    AgentAssetCapability,
    AgentAssetCapabilityStore,
)


REDIS_INTEGRATION_URL = os.getenv(
    "REDIS_INTEGRATION_URL",
    "",
).strip()


@pytest.mark.skipif(
    not REDIS_INTEGRATION_URL,
    reason="REDIS_INTEGRATION_URL is not configured",
)
def test_real_redis_capability_concurrency_sliding_ttl_and_revocation() -> None:
    asyncio.run(_run_real_redis_scenario())


async def _run_real_redis_scenario() -> None:
    redis = Redis.from_url(
        REDIS_INTEGRATION_URL,
        decode_responses=True,
    )
    capability = AgentAssetCapability(
        owner_user_id=uuid4(),
        file_id=uuid4(),
        object_key=f"integration/{uuid4()}/photo.png",
        purpose="model_image",
        content_type="image/png",
    )
    store = AgentAssetCapabilityStore(
        redis_client=redis,
        inactivity_ttl_seconds=1800,
    )
    try:
        issued = await asyncio.gather(
            *(
                store.issue_or_refresh(capability)
                for _index in range(20)
            )
        )
        tokens = {item.token for item in issued}
        assert len(tokens) == 1
        token = next(iter(tokens))
        logical_key = store.logical_key_for(capability)
        token_key = store.token_key_for(token)

        await redis.expire(logical_key, 60)
        await redis.expire(token_key, 60)
        assert await store.get(token) == capability
        assert 1 <= await redis.ttl(logical_key) <= 60
        assert 1 <= await redis.ttl(token_key) <= 60

        refreshed = await store.issue_or_refresh(capability)
        assert refreshed.token == token
        assert await redis.ttl(logical_key) > 1700
        assert await redis.ttl(token_key) > 1700

        await store.invalidate(
            owner_user_id=capability.owner_user_id,
            file_id=capability.file_id,
            object_key=capability.object_key,
        )
        assert await store.get(token) is None
    finally:
        await store.invalidate(
            owner_user_id=capability.owner_user_id,
            file_id=capability.file_id,
            object_key=capability.object_key,
        )
        await redis.aclose()
