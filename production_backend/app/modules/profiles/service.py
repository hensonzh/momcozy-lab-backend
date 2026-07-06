from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ..audit import AuditService, IdempotencyService, parse_idempotency_response_ref, request_hash
from .models import InfantProfile, UserProfile
from .repository import ProfileRepository


INFANT_CREATE_IDEMPOTENCY_SCOPE = "profiles.infants.create"


class ProfileService:
    def __init__(
        self,
        *,
        repository: ProfileRepository,
        audit_service: AuditService | None = None,
        idempotency_service: IdempotencyService | None = None,
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service
        self.idempotency_service = idempotency_service

    async def get_user_profile(self, *, user_id: UUID) -> UserProfile | None:
        return await self.repository.get_user_profile(user_id=user_id)

    async def update_user_profile(self, *, user_id: UUID, values: dict[str, Any], request_id: str = "") -> UserProfile:
        profile = await self.repository.upsert_user_profile(user_id=user_id, values=values)
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=user_id,
                action="profiles.user.update",
                resource_type="user_profile",
                resource_id=str(profile.id),
                request_id=request_id,
                details={"fields": sorted(values.keys())},
            )
        return profile

    async def list_infants(self, *, owner_user_id: UUID) -> list[InfantProfile]:
        return await self.repository.list_infants(owner_user_id=owner_user_id)

    async def create_infant(
        self,
        *,
        owner_user_id: UUID,
        infant_name: str,
        sex: str = "",
        birth_date: date | None = None,
        request_id: str = "",
        idempotency_key: str | None = None,
    ) -> InfantProfile:
        idempotency_record = None
        normalized_name = infant_name.strip()
        if not normalized_name:
            raise ApiError(code="validation_failed", message="infant_name is required.", status=422)

        if idempotency_key:
            if self.idempotency_service is None:
                raise ApiError(code="internal_error", message="Idempotency service is not configured.", status=500)
            decision = await self.idempotency_service.reserve(
                actor_user_id=owner_user_id,
                scope=INFANT_CREATE_IDEMPOTENCY_SCOPE,
                key=idempotency_key,
                request_hash=request_hash(
                    {
                        "infant_name": normalized_name,
                        "sex": sex or "",
                        "birth_date": str(birth_date or ""),
                    }
                ),
                expires_at=_idempotency_expires_at(),
            )
            idempotency_record = decision.record
            if decision.status == "replay":
                return await self._replay_create_infant(
                    owner_user_id=owner_user_id,
                    response_ref=idempotency_record.response_ref,
                )

        infant = await self.repository.create_infant(
            owner_user_id=owner_user_id,
            infant_name=normalized_name,
            sex=sex or "",
            birth_date=birth_date,
        )
        if idempotency_record is not None and self.idempotency_service is not None:
            await self.idempotency_service.mark_completed(record=idempotency_record, response_ref=str(infant.id))
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action="profiles.infant.create",
                resource_type="infant_profile",
                resource_id=str(infant.id),
                request_id=request_id,
                details={"infant_name": normalized_name},
            )
        return infant

    async def _replay_create_infant(self, *, owner_user_id: UUID, response_ref: str) -> InfantProfile:
        infant_id = parse_idempotency_response_ref(response_ref)
        infant = await self.repository.get_infant_for_owner(infant_id=infant_id, owner_user_id=owner_user_id)
        if infant is None:
            raise ApiError(code="conflict", message="Idempotency response resource is unavailable.", status=409)
        return infant


def _idempotency_expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=24)
