from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from threading import Lock
from time import monotonic
from typing import Protocol, cast

from fastapi import Request

from ..modules.auth.account_service import normalize_email
from .errors import ApiError
from .settings import Settings


RATE_LIMIT_EXEMPT_PATHS = {
    "/.well-known/jwks.json",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/v1/health/live",
    "/v1/health/ready",
    "/v1/health/metrics",
}
AUTH_ENTRY_PATHS = {"/v1/auth/logout-session","/v1/auth/signup", "/v1/auth/register", "/v1/auth/login", "/v1/auth/google", "/v1/auth/google/link",
                    "/v1/auth/verify-email", "/v1/auth/resend-verification", "/v1/auth/forgot-password", "/v1/auth/reset-password", "/v1/auth/invite-login"}
WORKBENCH_LOGIN_PATHS = {"/v1/ibclc/auth/login", "/v1/ibclc/auth/verify"}


class RedisRateLimitClient(Protocol):
    async def eval(self, script: str, numkeys: int, *args: str | int) -> object: ...


_INCREMENT_WITH_TTL = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return count
"""


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
    if (not settings.rate_limit_enabled and request.url.path not in WORKBENCH_LOGIN_PATHS | AUTH_ENTRY_PATHS) or request.url.path in RATE_LIMIT_EXEMPT_PATHS:
        return RateLimitDecision(
            allowed=True,
            limit=settings.rate_limit_requests,
            remaining=settings.rate_limit_requests,
            retry_after_seconds=0,
        )

    key, limit, window_seconds = _rate_limit_profile(request, settings)
    count = await _increment_counter(
        request=request,
        key=key,
        window_seconds=window_seconds,
    )
    if request.url.path in AUTH_ENTRY_PATHS:
        try:
            payload = await request.json()
            email = normalize_email(payload.get("email"))
        except (ValueError, AttributeError, ApiError):
            email = ""
        if email:
            email_count = await _increment_counter(request=request, key=f"rate-limit:auth-email:{_stable_hash(email)}", window_seconds=900)
            if email_count > 30:
                return RateLimitDecision(False, 30, 0, 900)
    remaining = max(limit - count, 0)
    return RateLimitDecision(
        allowed=count <= limit,
        limit=limit,
        remaining=remaining,
        retry_after_seconds=window_seconds if count > limit else 0,
    )


async def _increment_counter(*, request: Request, key: str, window_seconds: int) -> int:
    redis_client = cast(RedisRateLimitClient | None, getattr(request.app.state, "redis_client", None))
    if redis_client is not None:
        try:
            return int(cast(int, await redis_client.eval(_INCREMENT_WITH_TTL, 1, key, window_seconds)))
        except Exception:
            if request.app.state.settings.is_production:
                return 1_000_000_000

    if request.app.state.settings.is_production and redis_client is None:
        return 1_000_000_000
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


def _rate_limit_profile(
    request: Request,
    settings: Settings,
) -> tuple[str, int, int]:
    if request.url.path in WORKBENCH_LOGIN_PATHS | AUTH_ENTRY_PATHS:
        client_host = request.client.host if request.client else "unknown"
        limit = min(settings.rate_limit_requests, 20) if settings.rate_limit_enabled else 20
        window = settings.rate_limit_window_seconds if settings.rate_limit_enabled and settings.rate_limit_requests < 20 else 60
        return f"rate-limit:auth-ip:{client_host}", limit, window
    service_key = request.headers.get("x-service-key", "")
    configured_key = settings.agent_runtime_service_api_key
    if (
        request.url.path.startswith("/v1/internal/agent/")
        and service_key
        and configured_key
        and hmac.compare_digest(service_key, configured_key)
    ):
        return (
            f"rate-limit:agent-runtime:{_stable_hash(service_key)}",
            settings.agent_runtime_rate_limit_requests,
            settings.agent_runtime_rate_limit_window_seconds,
        )
    return (
        _rate_limit_key(request),
        settings.rate_limit_requests,
        settings.rate_limit_window_seconds,
    )


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
