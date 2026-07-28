from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from .agent_contracts import AgentFilePurpose


AGENT_FILE_URL_CACHE_NAMESPACE = "product:agent-file-url:v1"
CACHE_PAYLOAD_VERSION = 1
MAX_MODEL_URL_LENGTH = 8192
LOGGER = logging.getLogger("production_backend.files.agent_url_cache")


@dataclass(frozen=True)
class CachedAgentFileUrl:
    model_url: str
    expires_at: datetime


class AgentFileUrlCache:
    """Reconstructable Redis cache for stable, bounded model-fetch URLs."""

    def __init__(
        self,
        *,
        redis_client: Any | None,
        reuse_ttl_seconds: int,
        minimum_remaining_seconds: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if reuse_ttl_seconds < 60:
            raise ValueError("reuse_ttl_seconds must be at least 60")
        if minimum_remaining_seconds < 1:
            raise ValueError("minimum_remaining_seconds must be positive")
        self.redis_client = redis_client
        self.reuse_ttl_seconds = reuse_ttl_seconds
        self.minimum_remaining_seconds = minimum_remaining_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def key_for(
        self,
        *,
        owner_user_id: UUID,
        file_id: UUID,
        purpose: AgentFilePurpose,
        object_key: str,
    ) -> str:
        object_version = hashlib.sha256(object_key.encode("utf-8")).hexdigest()
        return (
            f"{AGENT_FILE_URL_CACHE_NAMESPACE}:"
            f"{owner_user_id}:{file_id}:{purpose}:{object_version}"
        )

    async def get(
        self,
        *,
        owner_user_id: UUID,
        file_id: UUID,
        purpose: AgentFilePurpose,
        object_key: str,
    ) -> CachedAgentFileUrl | None:
        if self.redis_client is None:
            _log_cache_event("disabled")
            return None
        key = self.key_for(
            owner_user_id=owner_user_id,
            file_id=file_id,
            purpose=purpose,
            object_key=object_key,
        )
        try:
            raw = await self.redis_client.get(key)
        except Exception as exc:
            _log_cache_event("read_error", exception_type=type(exc).__name__)
            return None
        if raw is None:
            _log_cache_event("miss")
            return None

        cached = _decode_cache_payload(raw)
        if cached is None or not is_safe_model_url(cached.model_url):
            await self._delete_keys(key)
            _log_cache_event("invalid")
            return None
        minimum_expiry = _aware_utc(self.clock()) + timedelta(
            seconds=self.minimum_remaining_seconds
        )
        if cached.expires_at <= minimum_expiry:
            await self._delete_keys(key)
            _log_cache_event("stale")
            return None
        _log_cache_event("hit")
        return cached

    async def publish_or_get(
        self,
        *,
        owner_user_id: UUID,
        file_id: UUID,
        purpose: AgentFilePurpose,
        object_key: str,
        candidate: CachedAgentFileUrl,
    ) -> CachedAgentFileUrl:
        if self.redis_client is None:
            return candidate
        key = self.key_for(
            owner_user_id=owner_user_id,
            file_id=file_id,
            purpose=purpose,
            object_key=object_key,
        )
        payload = _encode_cache_payload(candidate)
        try:
            stored = await self.redis_client.set(
                key,
                payload,
                ex=self.reuse_ttl_seconds,
                nx=True,
            )
        except Exception as exc:
            _log_cache_event("write_error", exception_type=type(exc).__name__)
            return candidate
        if stored:
            _log_cache_event("store")
            return candidate

        existing = await self.get(
            owner_user_id=owner_user_id,
            file_id=file_id,
            purpose=purpose,
            object_key=object_key,
        )
        return existing or candidate

    async def invalidate(
        self,
        *,
        owner_user_id: UUID,
        file_id: UUID,
        object_key: str,
    ) -> None:
        if self.redis_client is None:
            return
        purposes: tuple[AgentFilePurpose, ...] = ("model_image", "model_file")
        keys = tuple(
            self.key_for(
                owner_user_id=owner_user_id,
                file_id=file_id,
                purpose=purpose,
                object_key=object_key,
            )
            for purpose in purposes
        )
        await self._delete_keys(*keys)
        _log_cache_event("invalidate")

    async def _delete_keys(self, *keys: str) -> None:
        redis_client = self.redis_client
        if redis_client is None:
            return
        try:
            await redis_client.delete(*keys)
        except Exception as exc:
            _log_cache_event("delete_error", exception_type=type(exc).__name__)


def is_safe_model_url(value: str) -> bool:
    if not value or len(value) > MAX_MODEL_URL_LENGTH:
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None
    )


def _encode_cache_payload(value: CachedAgentFileUrl) -> str:
    return json.dumps(
        {
            "version": CACHE_PAYLOAD_VERSION,
            "model_url": value.model_url,
            "expires_at": _aware_utc(value.expires_at).isoformat(),
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _decode_cache_payload(raw: object) -> CachedAgentFileUrl | None:
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(str(raw))
        if not isinstance(payload, dict) or payload.get("version") != CACHE_PAYLOAD_VERSION:
            return None
        model_url = payload.get("model_url")
        expires_at = payload.get("expires_at")
        if not isinstance(model_url, str) or not isinstance(expires_at, str):
            return None
        parsed_expiry = _aware_utc(datetime.fromisoformat(expires_at))
    except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return CachedAgentFileUrl(model_url=model_url, expires_at=parsed_expiry)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _log_cache_event(outcome: str, *, exception_type: str = "") -> None:
    payload = {
        "event": "agent_file_url_cache",
        "outcome": outcome,
    }
    if exception_type:
        payload["exception_type"] = exception_type
    LOGGER.info(json.dumps(payload, separators=(",", ":"), sort_keys=True))
