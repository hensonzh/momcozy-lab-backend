from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from app.core.errors import ApiError


MODEL_IMAGE_CONTENT_TYPES = frozenset(
    {
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)
MAX_MODEL_IMAGE_URL_LENGTH = 8192


class AgentImageAccessService:
    def __init__(
        self,
        *,
        repository: Any,
        file_repository: Any,
        object_storage: Any,
        url_ttl: timedelta,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if url_ttl.total_seconds() < 60:
            raise ValueError("url_ttl must be at least 60 seconds")
        self.repository = repository
        self.file_repository = file_repository
        self.object_storage = object_storage
        self.url_ttl = url_ttl
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def ensure_for_thread(
        self,
        *,
        thread_id: UUID,
        owner_user_id: UUID,
        asset_id: UUID,
    ) -> str:
        thread = await self.repository.get_thread_for_owner(
            thread_id=thread_id,
            owner_user_id=owner_user_id,
        )
        if thread is None:
            raise ApiError(code="invalid_agent_attachment", message="Agent image thread is unavailable.", status=422)

        image = await self.file_repository.get_for_owner(
            file_id=asset_id,
            owner_user_id=owner_user_id,
        )
        if not _active_model_image(image):
            raise ApiError(code="invalid_agent_attachment", message="Image asset is not an active owned image.", status=422)

        now = _aware_utc(self.clock())
        existing = await self.repository.get_image_access(thread_id=thread_id, asset_id=asset_id)
        if existing is not None and _aware_utc(existing.expires_at) > now and _is_https_url(existing.image_url):
            return str(existing.image_url)

        create_url = getattr(self.object_storage, "create_presigned_get_url", None)
        if not callable(create_url):
            raise _image_url_unavailable()
        try:
            image_url = str(
                await create_url(
                    key=str(image.object_key),
                    expires_in_seconds=int(self.url_ttl.total_seconds()),
                )
            )
        except Exception as exc:
            raise _image_url_unavailable() from exc
        if not _is_https_url(image_url):
            raise _image_url_unavailable()

        expires_at = now + self.url_ttl
        access = await self.repository.upsert_image_access(
            thread_id=thread_id,
            asset_id=asset_id,
            image_url=image_url,
            expires_at=expires_at,
        )
        return str(access.image_url)

    async def resolve_for_provider(
        self,
        *,
        thread_id: str,
        actor_user_id: str,
        asset_id: str,
    ) -> str:
        try:
            parsed_thread_id = UUID(thread_id)
            parsed_owner_user_id = UUID(actor_user_id)
            parsed_asset_id = UUID(asset_id)
        except (TypeError, ValueError) as exc:
            raise ApiError(code="invalid_agent_attachment", message="Image asset reference is invalid.", status=422) from exc
        return await self.ensure_for_thread(
            thread_id=parsed_thread_id,
            owner_user_id=parsed_owner_user_id,
            asset_id=parsed_asset_id,
        )


def _active_model_image(image: Any) -> bool:
    return bool(
        image is not None
        and getattr(image, "deleted_at", None) is None
        and str(getattr(image, "status", "") or "") == "active"
        and str(getattr(image, "content_type", "") or "").lower() in MODEL_IMAGE_CONTENT_TYPES
    )


def _is_https_url(value: Any) -> bool:
    image_url = str(value or "").strip()
    if not image_url or len(image_url) > MAX_MODEL_IMAGE_URL_LENGTH:
        return False
    parsed = urlsplit(image_url)
    return parsed.scheme == "https" and bool(parsed.netloc) and parsed.username is None and parsed.password is None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _image_url_unavailable() -> ApiError:
    return ApiError(
        code="agent_image_url_unavailable",
        message="A public HTTPS image URL could not be created.",
        status=503,
    )
