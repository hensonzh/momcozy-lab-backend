from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from ...core.errors import ApiError
from .agent_asset_capability import (
    AgentAssetCapability,
    AgentAssetCapabilityStore,
    AgentAssetCapabilityUnavailable,
)
from .agent_contracts import AgentFilePurpose


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


@dataclass(frozen=True)
class AgentModelAsset:
    body: bytes
    content_type: str


class AgentFileAccessService:
    """Product-owned authorization and opaque model capability issuer."""

    def __init__(
        self,
        *,
        repository: Any,
        capability_store: AgentAssetCapabilityStore,
        public_base_url: str,
    ) -> None:
        self.repository = repository
        self.capability_store = capability_store
        self.public_base_url = public_base_url

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
        content_type = _authorized_content_type(
            file_object=file_object,
            purpose=purpose,
        )
        capability = AgentAssetCapability(
            owner_user_id=owner_user_id,
            file_id=file_id,
            object_key=str(file_object.object_key),
            purpose=purpose,
            content_type=content_type,
        )
        try:
            issued = await self.capability_store.issue_or_refresh(
                capability
            )
        except AgentAssetCapabilityUnavailable as exc:
            raise _capability_unavailable() from exc
        model_url = _model_asset_url(
            public_base_url=self.public_base_url,
            token=issued.token,
        )
        if model_url is None:
            raise _capability_unavailable()
        return AgentFileAccess(
            file_id=file_object.id,
            content_type=content_type,
            original_filename=str(
                file_object.original_filename or ""
            )[:255],
            model_url=model_url,
            expires_at=issued.expires_at,
        )


class AgentLocalImageService:
    """Return owner-scoped image bytes to the local Runtime without a public URL."""

    def __init__(self, *, repository: Any, object_storage: Any, max_bytes: int) -> None:
        self.repository = repository
        self.object_storage = object_storage
        self.max_bytes = max_bytes

    async def fetch(self, *, owner_user_id: UUID, file_id: UUID) -> AgentModelAsset:
        file_object = await self.repository.get_for_owner(
            file_id=file_id, owner_user_id=owner_user_id,
        )
        content_type = _authorized_content_type(
            file_object=file_object, purpose="model_image",
        )
        if file_object.id != file_id or file_object.owner_user_id != owner_user_id:
            raise _invalid_attachment()
        if file_object.size_bytes > self.max_bytes or file_object.size_bytes <= 0:
            raise _invalid_attachment()
        body = await self.object_storage.get_bytes(key=file_object.object_key)
        if not body or len(body) > self.max_bytes:
            raise _invalid_attachment()
        return AgentModelAsset(body=bytes(body), content_type=content_type)


class AgentModelAssetService:
    """Redeems one capability after revalidating authoritative file state."""

    def __init__(
        self,
        *,
        repository: Any,
        object_storage: Any,
        capability_store: AgentAssetCapabilityStore,
    ) -> None:
        self.repository = repository
        self.object_storage = object_storage
        self.capability_store = capability_store

    async def fetch(self, *, token: str) -> AgentModelAsset:
        try:
            capability = await self.capability_store.get(token)
        except AgentAssetCapabilityUnavailable as exc:
            raise _capability_unavailable() from exc
        if capability is None:
            raise _capability_not_found()

        file_object = await self.repository.get_for_owner(
            file_id=capability.file_id,
            owner_user_id=capability.owner_user_id,
        )
        if not _capability_matches_file(
            capability=capability,
            file_object=file_object,
        ):
            raise _capability_not_found()
        try:
            body = await self.object_storage.get_bytes(
                key=capability.object_key
            )
        except Exception as exc:
            raise _capability_unavailable() from exc
        return AgentModelAsset(
            body=bytes(body),
            content_type=capability.content_type,
        )


def _authorized_content_type(
    *,
    file_object: Any,
    purpose: AgentFilePurpose,
) -> str:
    if not _is_active(file_object):
        raise _invalid_attachment()
    content_type = str(
        file_object.content_type or ""
    ).strip().lower()
    allowed_types = (
        MODEL_IMAGE_CONTENT_TYPES
        if purpose == "model_image"
        else MODEL_FILE_CONTENT_TYPES
    )
    if content_type not in allowed_types:
        raise _invalid_attachment()
    return content_type


def _capability_matches_file(
    *,
    capability: AgentAssetCapability,
    file_object: Any,
) -> bool:
    if not _is_active(file_object):
        return False
    if (
        getattr(file_object, "id", None) != capability.file_id
        or getattr(file_object, "owner_user_id", None)
        != capability.owner_user_id
        or str(getattr(file_object, "object_key", "") or "")
        != capability.object_key
    ):
        return False
    try:
        content_type = _authorized_content_type(
            file_object=file_object,
            purpose=capability.purpose,
        )
    except ApiError:
        return False
    return content_type == capability.content_type


def _is_active(file_object: Any) -> bool:
    return bool(
        file_object is not None
        and getattr(file_object, "deleted_at", None) is None
        and str(
            getattr(file_object, "status", "") or ""
        )
        == "active"
    )


def _model_asset_url(
    *,
    public_base_url: str,
    token: str,
) -> str | None:
    value = str(public_base_url or "").strip().rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        return None
    return f"{value}/v1/model-assets/{token}"


def _invalid_attachment() -> ApiError:
    return ApiError(
        code="invalid_agent_attachment",
        message=(
            "File is not an active owned attachment for "
            "the requested purpose."
        ),
        status=422,
    )


def _capability_not_found() -> ApiError:
    return ApiError(
        code="not_found",
        message="Model asset not found.",
        status=404,
    )


def _capability_unavailable() -> ApiError:
    return ApiError(
        code="agent_model_asset_unavailable",
        message="Model asset capability is temporarily unavailable.",
        status=503,
    )
