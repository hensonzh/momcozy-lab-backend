from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from .agent_contracts import AgentFilePurpose


AGENT_ASSET_CAPABILITY_NAMESPACE = "product:agent-asset-capability:v1"
CAPABILITY_PAYLOAD_VERSION = 1
CAPABILITY_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
MAX_ISSUE_ATTEMPTS = 3
LOGGER = logging.getLogger(
    "production_backend.files.agent_asset_capability"
)


@dataclass(frozen=True)
class AgentAssetCapability:
    owner_user_id: UUID
    file_id: UUID
    object_key: str
    purpose: AgentFilePurpose
    content_type: str

    @property
    def asset_version(self) -> str:
        return hashlib.sha256(
            self.object_key.encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class IssuedAgentAssetCapability:
    token: str
    expires_at: datetime


class AgentAssetCapabilityUnavailable(RuntimeError):
    pass


class AgentAssetCapabilityStore:
    """Redis-backed opaque bearer capabilities with authorized sliding renewal."""

    def __init__(
        self,
        *,
        redis_client: Any | None,
        inactivity_ttl_seconds: int,
        clock: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        if inactivity_ttl_seconds < 60:
            raise ValueError(
                "inactivity_ttl_seconds must be at least 60"
            )
        self.redis_client = redis_client
        self.inactivity_ttl_seconds = inactivity_ttl_seconds
        self.clock = clock or (
            lambda: datetime.now(timezone.utc)
        )
        self.token_factory = token_factory or (
            lambda: secrets.token_urlsafe(32)
        )

    def logical_key_for(
        self,
        capability: AgentAssetCapability,
    ) -> str:
        identity = _canonical_json(
            {
                "owner_user_id": str(capability.owner_user_id),
                "file_id": str(capability.file_id),
                "purpose": capability.purpose,
                "asset_version": capability.asset_version,
            }
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return (
            f"{AGENT_ASSET_CAPABILITY_NAMESPACE}:asset:{digest}"
        )

    def token_key_for(self, token: str) -> str:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return (
            f"{AGENT_ASSET_CAPABILITY_NAMESPACE}:token:{digest}"
        )

    async def issue_or_refresh(
        self,
        capability: AgentAssetCapability,
    ) -> IssuedAgentAssetCapability:
        redis_client = self.redis_client
        if redis_client is None:
            raise AgentAssetCapabilityUnavailable(
                "Redis capability storage is unavailable"
            )
        issued_at = _aware_utc(self.clock())
        logical_key = self.logical_key_for(capability)
        payload = _encode_capability(capability)
        try:
            existing_token = await self._get_and_refresh_existing(
                logical_key=logical_key,
                expected_payload=payload,
            )
            if existing_token is not None:
                _log_capability_event("refresh")
                return self._issued(
                    existing_token,
                    issued_at=issued_at,
                )

            for _attempt in range(MAX_ISSUE_ATTEMPTS):
                token = self.token_factory()
                if not is_valid_capability_token(token):
                    raise AgentAssetCapabilityUnavailable(
                        "Capability token factory returned an invalid token"
                    )
                token_key = self.token_key_for(token)
                token_stored = await redis_client.set(
                    token_key,
                    payload,
                    ex=self.inactivity_ttl_seconds,
                    nx=True,
                )
                if not token_stored:
                    continue
                pointer = _encode_pointer(token)
                pointer_stored = await redis_client.set(
                    logical_key,
                    pointer,
                    ex=self.inactivity_ttl_seconds,
                    nx=True,
                )
                if pointer_stored:
                    _log_capability_event("issue")
                    return self._issued(
                        token,
                        issued_at=issued_at,
                    )

                await self._delete_keys(token_key)
                existing_token = (
                    await self._get_and_refresh_existing(
                        logical_key=logical_key,
                        expected_payload=payload,
                    )
                )
                if existing_token is not None:
                    _log_capability_event("race_reuse")
                    return self._issued(
                        existing_token,
                        issued_at=issued_at,
                    )
            raise AgentAssetCapabilityUnavailable(
                "Could not allocate a unique capability token"
            )
        except AgentAssetCapabilityUnavailable:
            raise
        except Exception as exc:
            _log_capability_event(
                "issue_error",
                exception_type=type(exc).__name__,
            )
            raise AgentAssetCapabilityUnavailable(
                "Redis capability storage is unavailable"
            ) from exc

    async def get(
        self,
        token: str,
    ) -> AgentAssetCapability | None:
        if not is_valid_capability_token(token):
            return None
        redis_client = self.redis_client
        if redis_client is None:
            raise AgentAssetCapabilityUnavailable(
                "Redis capability storage is unavailable"
            )
        token_key = self.token_key_for(token)
        try:
            raw = await redis_client.get(token_key)
        except Exception as exc:
            _log_capability_event(
                "read_error",
                exception_type=type(exc).__name__,
            )
            raise AgentAssetCapabilityUnavailable(
                "Redis capability storage is unavailable"
            ) from exc
        if raw is None:
            _log_capability_event("miss")
            return None
        capability = _decode_capability(raw)
        if capability is None:
            await self._delete_keys(token_key)
            _log_capability_event("invalid")
            return None
        _log_capability_event("hit")
        return capability

    async def invalidate(
        self,
        *,
        owner_user_id: UUID,
        file_id: UUID,
        object_key: str,
    ) -> None:
        redis_client = self.redis_client
        if redis_client is None:
            return
        for purpose in ("model_image", "model_file"):
            capability = AgentAssetCapability(
                owner_user_id=owner_user_id,
                file_id=file_id,
                object_key=object_key,
                purpose=purpose,
                content_type="",
            )
            logical_key = self.logical_key_for(capability)
            try:
                raw_pointer = await redis_client.get(logical_key)
                token = _decode_pointer(raw_pointer)
                keys = [logical_key]
                if token is not None:
                    keys.append(self.token_key_for(token))
                await self._delete_keys(*keys)
            except Exception as exc:
                _log_capability_event(
                    "invalidate_error",
                    exception_type=type(exc).__name__,
                )
        _log_capability_event("invalidate")

    async def _get_and_refresh_existing(
        self,
        *,
        logical_key: str,
        expected_payload: str,
    ) -> str | None:
        redis_client = self.redis_client
        if redis_client is None:
            return None
        raw_pointer = await redis_client.get(logical_key)
        token = _decode_pointer(raw_pointer)
        if token is None:
            if raw_pointer is not None:
                await self._delete_keys(logical_key)
            return None
        token_key = self.token_key_for(token)
        existing_payload = await redis_client.get(token_key)
        if _decode_text(existing_payload) != expected_payload:
            await self._delete_keys(logical_key, token_key)
            return None
        logical_refreshed = await redis_client.expire(
            logical_key,
            self.inactivity_ttl_seconds,
        )
        token_refreshed = await redis_client.expire(
            token_key,
            self.inactivity_ttl_seconds,
        )
        if not logical_refreshed or not token_refreshed:
            await self._delete_keys(logical_key, token_key)
            return None
        return token

    async def _delete_keys(self, *keys: str) -> None:
        redis_client = self.redis_client
        if redis_client is None or not keys:
            return
        await redis_client.delete(*keys)

    def _issued(
        self,
        token: str,
        *,
        issued_at: datetime,
    ) -> IssuedAgentAssetCapability:
        return IssuedAgentAssetCapability(
            token=token,
            expires_at=issued_at
            + timedelta(
                seconds=self.inactivity_ttl_seconds
            ),
        )


def is_valid_capability_token(token: str) -> bool:
    return bool(CAPABILITY_TOKEN_PATTERN.fullmatch(token))


def _encode_capability(
    capability: AgentAssetCapability,
) -> str:
    return _canonical_json(
        {
            "schema_version": CAPABILITY_PAYLOAD_VERSION,
            "owner_user_id": str(capability.owner_user_id),
            "file_id": str(capability.file_id),
            "object_key": capability.object_key,
            "asset_version": capability.asset_version,
            "purpose": capability.purpose,
            "content_type": capability.content_type,
        }
    )


def _decode_capability(
    raw: object,
) -> AgentAssetCapability | None:
    try:
        payload = json.loads(_decode_text(raw))
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version")
            != CAPABILITY_PAYLOAD_VERSION
        ):
            return None
        capability = AgentAssetCapability(
            owner_user_id=UUID(str(payload["owner_user_id"])),
            file_id=UUID(str(payload["file_id"])),
            object_key=str(payload["object_key"]),
            purpose=str(payload["purpose"]),  # type: ignore[arg-type]
            content_type=str(payload["content_type"]),
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ):
        return None
    if (
        capability.purpose
        not in {"model_image", "model_file"}
        or not capability.object_key
        or not capability.content_type
        or payload.get("asset_version")
        != capability.asset_version
    ):
        return None
    return capability


def _encode_pointer(token: str) -> str:
    return _canonical_json(
        {
            "schema_version": CAPABILITY_PAYLOAD_VERSION,
            "token": token,
            "token_sha256": hashlib.sha256(
                token.encode("utf-8")
            ).hexdigest(),
        }
    )


def _decode_pointer(raw: object) -> str | None:
    if raw is None:
        return None
    try:
        payload = json.loads(_decode_text(raw))
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version")
            != CAPABILITY_PAYLOAD_VERSION
        ):
            return None
        token = str(payload["token"])
        digest = str(payload["token_sha256"])
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ):
        return None
    if (
        not is_valid_capability_token(token)
        or not secrets.compare_digest(
            digest,
            hashlib.sha256(token.encode("utf-8")).hexdigest(),
        )
    ):
        return None
    return token


def _decode_text(raw: object) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    return str(raw)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        separators=(",", ":"),
        sort_keys=True,
    )


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _log_capability_event(
    outcome: str,
    *,
    exception_type: str = "",
) -> None:
    payload = {
        "event": "agent_asset_capability",
        "outcome": outcome,
    }
    if exception_type:
        payload["exception_type"] = exception_type
    LOGGER.info(
        json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
