from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from .agent_contracts import AgentFilePurpose
from .agent_url_cache import (
    AgentFileUrlCache,
    CachedAgentFileUrl,
    is_safe_model_url,
)


MODEL_IMAGE_CONTENT_TYPES = frozenset(
    {
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)
MODEL_FILE_CONTENT_TYPES = frozenset({"application/pdf"})
@dataclass(frozen=True)
class AgentFileAccess:
    file_id: UUID
    content_type: str
    original_filename: str
    model_url: str
    expires_at: datetime


class AgentFileAccessService:
    """Product-owned owner check and short-lived model URL issuer."""

    def __init__(
        self,
        *,
        repository: Any,
        object_storage: Any,
        url_ttl_seconds: int,
        url_cache: AgentFileUrlCache | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if url_ttl_seconds < 60:
            raise ValueError("url_ttl_seconds must be at least 60")
        self.repository = repository
        self.object_storage = object_storage
        self.url_ttl_seconds = url_ttl_seconds
        self.url_cache = url_cache
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def resolve(
        self,
        *,
        owner_user_id: UUID,
        file_id: UUID,
        purpose: AgentFilePurpose,
    ) -> AgentFileAccess:
        file_object = await self.repository.get_for_owner(
            file_id=file_id,
            owner_user_id=owner_user_id,
        )
        if not _is_active(file_object):
            raise _invalid_attachment()

        content_type = str(file_object.content_type or "").strip().lower()
        allowed_types = (
            MODEL_IMAGE_CONTENT_TYPES
            if purpose == "model_image"
            else MODEL_FILE_CONTENT_TYPES
        )
        if content_type not in allowed_types:
            raise _invalid_attachment()

        object_key = str(file_object.object_key)
        if self.url_cache is not None:
            cached = await self.url_cache.get(
                owner_user_id=owner_user_id,
                file_id=file_id,
                purpose=purpose,
                object_key=object_key,
            )
            if cached is not None:
                return _to_access(
                    file_object=file_object,
                    content_type=content_type,
                    cached=cached,
                )

        try:
            model_url = str(
                await self.object_storage.create_presigned_get_url(
                    key=object_key,
                    expires_in_seconds=self.url_ttl_seconds,
                )
            )
        except Exception as exc:
            raise _url_unavailable() from exc
        if not is_safe_model_url(model_url):
            raise _url_unavailable()

        now = _aware_utc(self.clock())
        selected = CachedAgentFileUrl(
            model_url=model_url,
            expires_at=now + timedelta(seconds=self.url_ttl_seconds),
        )
        if self.url_cache is not None:
            selected = await self.url_cache.publish_or_get(
                owner_user_id=owner_user_id,
                file_id=file_id,
                purpose=purpose,
                object_key=object_key,
                candidate=selected,
            )
        return _to_access(
            file_object=file_object,
            content_type=content_type,
            cached=selected,
        )


def _is_active(file_object: Any) -> bool:
    return bool(
        file_object is not None
        and getattr(file_object, "deleted_at", None) is None
        and str(getattr(file_object, "status", "") or "") == "active"
    )


def _to_access(
    *,
    file_object: Any,
    content_type: str,
    cached: CachedAgentFileUrl,
) -> AgentFileAccess:
    return AgentFileAccess(
        file_id=file_object.id,
        content_type=content_type,
        original_filename=str(file_object.original_filename or "")[:255],
        model_url=cached.model_url,
        expires_at=cached.expires_at,
    )


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _invalid_attachment() -> ApiError:
    return ApiError(
        code="invalid_agent_attachment",
        message="File is not an active owned attachment for the requested purpose.",
        status=422,
    )


def _url_unavailable() -> ApiError:
    return ApiError(
        code="agent_file_url_unavailable",
        message="A public HTTPS model URL could not be created.",
        status=503,
    )
