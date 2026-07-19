from __future__ import annotations

import hashlib
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Protocol, cast

from fastapi import Request

from .settings import Settings


RATE_LIMIT_EXEMPT_PATHS = {
    "/docs",
    "/openapi.json",
    "/redoc",
    "/v1/health/live",
    "/v1/health/ready",
    "/v1/health/metrics",
}


class RedisRateLimitClient(Protocol):
    async def incr(self, name: str) -> int: ...

    async def expire(self, name: str, time: int) -> object: ...


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


class InMemoryRateLimitStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._buckets: dict[str, tuple[int, float]] = {}

    async def increment(self, *, key: str, window_seconds: int) -> int:
        now = monotonic()
        with self._lock:
            count, reset_at = self._buckets.get(key, (0, now + window_seconds))
            if reset_at <= now:
                count = 0
                reset_at = now + window_seconds
            count += 1
            self._buckets[key] = (count, reset_at)
            return count


async def check_rate_limit(request: Request, settings: Settings) -> RateLimitDecision:
    if not settings.rate_limit_enabled or request.url.path in RATE_LIMIT_EXEMPT_PATHS:
        return RateLimitDecision(
            allowed=True,
            limit=settings.rate_limit_requests,
            remaining=settings.rate_limit_requests,
            retry_after_seconds=0,
        )

    key = _rate_limit_key(request)
    count = await _increment_counter(request=request, key=key, window_seconds=settings.rate_limit_window_seconds)
    remaining = max(settings.rate_limit_requests - count, 0)
    return RateLimitDecision(
        allowed=count <= settings.rate_limit_requests,
        limit=settings.rate_limit_requests,
        remaining=remaining,
        retry_after_seconds=settings.rate_limit_window_seconds if count > settings.rate_limit_requests else 0,
    )


async def _increment_counter(*, request: Request, key: str, window_seconds: int) -> int:
    redis_client = cast(RedisRateLimitClient | None, getattr(request.app.state, "redis_client", None))
    if redis_client is not None:
        try:
            count = await redis_client.incr(key)
            if count == 1:
                await redis_client.expire(key, window_seconds)
            return count
        except Exception:
            pass

    store = cast(InMemoryRateLimitStore | None, getattr(request.app.state, "rate_limit_store", None))
    if store is None:
        store = InMemoryRateLimitStore()
        request.app.state.rate_limit_store = store
    return await store.increment(key=key, window_seconds=window_seconds)


def _rate_limit_key(request: Request) -> str:
    credential = _credential_identity(request)
    if credential:
        return f"rate-limit:credential:{credential}"
    client_host = request.client.host if request.client else "unknown"
    return f"rate-limit:ip:{client_host}"


def _credential_identity(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return _stable_hash(authorization)
    service_key = request.headers.get("x-service-key", "")
    if service_key:
        return _stable_hash(service_key)
    return ""


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
