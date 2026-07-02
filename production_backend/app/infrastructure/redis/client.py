from __future__ import annotations

from redis.asyncio import Redis

from ...core.settings import Settings


def create_redis_client(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


async def close_redis_client(client: Redis) -> None:
    await client.aclose()
