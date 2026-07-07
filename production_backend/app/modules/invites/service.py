from __future__ import annotations

import secrets
from datetime import datetime, timezone

from ...core.errors import ApiError
from .models import InviteCode
from .repository import InviteCodeRepository


INVITE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
INVITE_CODE_STATUS_ACTIVE = "active"
INVITE_CODE_STATUS_DISABLED = "disabled"


class InviteCodeService:
    def __init__(self, *, repository: InviteCodeRepository) -> None:
        self.repository = repository

    async def create_invite_code(
        self,
        *,
        code: str | None = None,
        label: str = "",
        assigned_to: str = "",
        expires_at: datetime | None = None,
        actor_service: str = "",
    ) -> InviteCode:
        normalized_label = _normalize_optional_text(label, field_name="label", max_length=120)
        normalized_assigned_to = _normalize_optional_text(assigned_to, field_name="assigned_to", max_length=320)
        if code is not None and str(code).strip():
            normalized_code = normalize_invite_code(code)
            if await self.repository.get_by_code(code=normalized_code) is not None:
                raise ApiError(code="conflict", message="Invite code already exists.", status=409)
            return await self.repository.create(
                code=normalized_code,
                label=normalized_label,
                assigned_to=normalized_assigned_to,
                expires_at=expires_at,
                created_by_service=actor_service,
            )

        for _attempt in range(10):
            generated = generate_invite_code()
            if await self.repository.get_by_code(code=generated) is None:
                return await self.repository.create(
                    code=generated,
                    label=normalized_label,
                    assigned_to=normalized_assigned_to,
                    expires_at=expires_at,
                    created_by_service=actor_service,
                )
        raise ApiError(code="conflict", message="Could not generate a unique invite code.", status=409)

    async def list_invite_codes(self, *, limit: int = 50) -> list[InviteCode]:
        if limit < 1 or limit > 100:
            raise ApiError(code="validation_failed", message="limit must be between 1 and 100.", status=422)
        return await self.repository.list_recent(limit=limit)

    async def disable_invite_code(self, *, code: str) -> InviteCode:
        normalized_code = normalize_invite_code(code)
        invite_code = await self.repository.get_by_code(code=normalized_code, for_update=True)
        if invite_code is None:
            raise ApiError(code="not_found", message="Invite code not found.", status=404)
        if invite_code.status == INVITE_CODE_STATUS_DISABLED:
            return invite_code
        return await self.repository.disable(invite_code=invite_code, disabled_at=_utcnow())


def generate_invite_code() -> str:
    return f"MCZ-{_random_chunk(4)}-{_random_chunk(4)}"


def normalize_invite_code(code: str) -> str:
    normalized = str(code or "").strip().upper()
    if not normalized:
        raise ApiError(code="validation_failed", message="Invite code is required.", status=422)
    if len(normalized) > 64:
        raise ApiError(code="validation_failed", message="Invite code is too long.", status=422)
    return normalized


def validate_invite_code_for_login(*, invite_code: InviteCode, device_id: str, now: datetime | None = None) -> None:
    if invite_code.status != INVITE_CODE_STATUS_ACTIVE:
        raise ApiError(code="permission_denied", message="Invite code is disabled.", status=403)
    if _is_expired(invite_code.expires_at, now=now or _utcnow()):
        raise ApiError(code="permission_denied", message="Invite code is expired.", status=403)
    bound_device_id = str(invite_code.bound_device_id or "").strip()
    if bound_device_id and bound_device_id != device_id:
        raise ApiError(
            code="permission_denied",
            message="Invite code is already bound to another device.",
            status=403,
        )


def _random_chunk(length: int) -> str:
    return "".join(secrets.choice(INVITE_CODE_ALPHABET) for _ in range(length))


def _normalize_optional_text(value: str | None, *, field_name: str, max_length: int) -> str:
    normalized = str(value or "").strip()
    if len(normalized) > max_length:
        raise ApiError(code="validation_failed", message=f"{field_name} is too long.", status=422)
    return normalized


def _is_expired(expires_at: datetime | None, *, now: datetime) -> bool:
    if expires_at is None:
        return False
    comparable_expires_at = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=timezone.utc)
    comparable_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    return comparable_expires_at <= comparable_now


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
