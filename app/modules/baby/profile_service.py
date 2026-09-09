from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from ...core.errors import ApiError
from ..audit.service import AuditService, IdempotencyService, parse_idempotency_response_ref, request_hash
from ..users.models import User
from .models import BabyRecord
from .profile_models import BabyProfile
from .profile_schemas import BabyProfileContent, BabyProfileList, BabyProfileRead, BabyProfileUpdate, BabyProfileWrite
from .repository import BabyRecordRepository
from .service import conflict, utc_now


class BabyProfileService:
    def __init__(self, repository: BabyRecordRepository, audit: AuditService, idempotency: IdempotencyService, *, now: Callable[[], datetime] = utc_now) -> None:
        self.repository, self.audit, self.idempotency, self.now = repository, audit, idempotency, now

    async def list(self, owner: UUID) -> BabyProfileList:
        values = await self.repository.session.scalars(select(BabyProfile).join(User, User.id == BabyProfile.owner_user_id).where(
            BabyProfile.owner_user_id == owner, BabyProfile.deleted_at.is_(None), User.deleted_at.is_(None), User.status == 'active',
        ).order_by(BabyProfile.created_at, BabyProfile.id))
        return BabyProfileList(items=[BabyProfileRead.model_validate(value) for value in values])

    async def create(self, owner: UUID, body: BabyProfileWrite, key: str, request_id: str) -> BabyProfile:
        actor = await self.repository.session.scalar(select(User.id).where(User.id == owner, User.status == 'active', User.deleted_at.is_(None)).with_for_update())
        if actor is None:
            raise ApiError(code='not_found', message='Account not found.', status=404)
        decision = await self.idempotency.reserve(actor_user_id=owner, scope='baby_profile.create', key=key,
            request_hash=request_hash(body.model_dump(mode='json')), expires_at=self.now() + timedelta(days=1))
        if decision.status == 'replay':
            return await self.repository.baby(owner, parse_idempotency_response_ref(decision.record.response_ref))
        self._validate_date(body)
        value = BabyProfile(owner_user_id=owner, **self._content(body), version=1, created_at=self.now(), updated_at=self.now())
        self.repository.session.add(value)
        await self.repository.session.flush()
        await self.idempotency.mark_completed(record=decision.record, response_ref=str(value.id))
        await self._audit(owner, value, 'create', request_id)
        return value

    async def update(self, owner: UUID, baby_id: UUID, body: BabyProfileUpdate, request_id: str) -> BabyProfile:
        value = await self.repository.baby(owner, baby_id, write=True)
        content = self._content(body)
        if value.version == body.expected_version + 1 and content == BabyProfileContent.model_validate(value).model_dump():
            return value
        if value.version != body.expected_version:
            raise conflict()
        if body.birth_date != value.birth_date:
            self._validate_date(body)
        if body.birth_date is not None and body.birth_date != value.birth_date:
            earliest = await self.repository.session.scalar(select(func.min(BabyRecord.recorded_on)).where(
                BabyRecord.owner_user_id == owner, BabyRecord.baby_id == baby_id, BabyRecord.deleted_at.is_(None)))
            if earliest is not None and earliest < body.birth_date:
                raise ApiError(code='birth_date_after_record', message='The birth date is after an existing growth or development record. Check those dates first.', status=409)
        for field, field_value in content.items():
            setattr(value, field, field_value)
        value.version += 1
        value.updated_at = self.now()
        await self.repository.session.flush()
        await self._audit(owner, value, 'update', request_id)
        return value

    @staticmethod
    def _content(body: BabyProfileWrite) -> dict[str, Any]:
        return body.model_dump(include=set(BabyProfileContent.model_fields))

    def _validate_date(self, body: BabyProfileWrite) -> None:
        if body.birth_date is not None and body.birth_date > self.now().astimezone(ZoneInfo(body.timezone)).date():
            raise ApiError(code='validation_failed', message='Birth date cannot be in the future in your timezone.', status=422)

    async def _audit(self, owner: UUID, value: BabyProfile, action: str, request_id: str) -> None:
        await self.audit.record(actor_user_id=owner, action=f'baby.profile.{action}', resource_type='baby_profile', resource_id=str(value.id),
            request_id=request_id, details={'version': value.version})
