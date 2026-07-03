import asyncio
from datetime import date
from uuid import UUID, uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.audit.models import IdempotencyKey
from production_backend.app.modules.audit.service import IdempotencyDecision
from production_backend.app.modules.profiles.models import InfantProfile, UserProfile
from production_backend.app.modules.profiles.router import _profile_read
from production_backend.app.modules.profiles.service import ProfileService


def test_profile_onboarding_main_flow_updates_profile_summary_and_infants_idempotently() -> None:
    asyncio.run(_run_profile_onboarding_main_flow())


async def _run_profile_onboarding_main_flow() -> None:
    owner_user_id = uuid4()
    other_user_id = uuid4()
    repository = InMemoryProfileRepository()
    audit_service = FlowAuditService()
    idempotency_service = FlowIdempotencyService()
    service = ProfileService(
        repository=repository,
        audit_service=audit_service,
        idempotency_service=idempotency_service,
    )

    assert await service.get_user_profile(user_id=owner_user_id) is None

    profile = await service.update_user_profile(
        user_id=owner_user_id,
        values={"display_name": "Mia", "age": 31, "delivery_date": date(2026, 8, 1)},
        request_id="req_profile",
    )
    summary = await service.update_status_summary(
        user_id=owner_user_id,
        lactation_advice="Keep pumping comfortable.",
        feeding_advice="Follow hunger cues.",
        daily_summary="Profile setup complete.",
        request_id="req_summary",
    )
    infant = await service.create_infant(
        owner_user_id=owner_user_id,
        infant_name=" Baby ",
        sex="female",
        birth_date=date(2026, 6, 1),
        request_id="req_infant",
        idempotency_key="idem-infant",
    )
    replayed_infant = await service.create_infant(
        owner_user_id=owner_user_id,
        infant_name="Baby",
        sex="female",
        birth_date=date(2026, 6, 1),
        idempotency_key="idem-infant",
    )
    await service.create_infant(
        owner_user_id=other_user_id,
        infant_name="Other baby",
        sex="male",
        birth_date=date(2026, 5, 1),
    )
    with pytest.raises(ApiError) as idempotency_conflict:
        await service.create_infant(
            owner_user_id=owner_user_id,
            infant_name="Baby",
            sex="female",
            birth_date=date(2026, 6, 2),
            idempotency_key="idem-infant",
        )

    infants = await service.list_infants(owner_user_id=owner_user_id)
    profile_response = _profile_read(summary, owner_user_id)

    assert profile.display_name == "Mia"
    assert profile.age == 31
    assert summary.daily_summary == "Profile setup complete."
    assert infant.infant_name == "Baby"
    assert replayed_infant.id == infant.id
    assert [item.id for item in infants] == [infant.id]
    assert profile_response.profile_onboarding_complete is True
    assert profile_response.profile_onboarding_skipped is False
    assert idempotency_conflict.value.code == "idempotency_conflict"
    assert [entry["action"] for entry in audit_service.entries] == [
        "profiles.user.update",
        "profiles.status_summary.update",
        "profiles.infant.create",
        "profiles.infant.create",
    ]
    assert idempotency_service.completed_response_refs == [str(infant.id)]


class InMemoryProfileRepository:
    def __init__(self) -> None:
        self.profiles: dict[UUID, UserProfile] = {}
        self.infants: list[InfantProfile] = []

    async def get_user_profile(self, *, user_id: UUID):
        return self.profiles.get(user_id)

    async def upsert_user_profile(self, *, user_id: UUID, values: dict):
        profile = self.profiles.get(user_id) or UserProfile(id=uuid4(), user_id=user_id)
        for field, value in values.items():
            setattr(profile, field, value)
        self.profiles[user_id] = profile
        return profile

    async def list_infants(self, *, owner_user_id: UUID):
        return [
            infant
            for infant in self.infants
            if infant.owner_user_id == owner_user_id and infant.status == "active" and infant.deleted_at is None
        ]

    async def get_infant_for_owner(self, *, infant_id: UUID, owner_user_id: UUID):
        return next(
            (
                infant
                for infant in self.infants
                if infant.id == infant_id
                and infant.owner_user_id == owner_user_id
                and infant.status == "active"
                and infant.deleted_at is None
            ),
            None,
        )

    async def create_infant(self, *, owner_user_id: UUID, infant_name: str, sex: str, birth_date: date | None):
        infant = InfantProfile(
            id=uuid4(),
            owner_user_id=owner_user_id,
            infant_name=infant_name,
            sex=sex,
            birth_date=birth_date,
            status="active",
            deleted_at=None,
        )
        self.infants.append(infant)
        return infant


class FlowIdempotencyService:
    def __init__(self) -> None:
        self.records: dict[tuple[UUID, str, str], IdempotencyKey] = {}
        self.completed_response_refs: list[str] = []

    async def reserve(self, *, actor_user_id: UUID, scope: str, key: str, request_hash: str, expires_at):
        record_key = (actor_user_id, scope, key)
        existing = self.records.get(record_key)
        if existing is None:
            record = IdempotencyKey(
                id=uuid4(),
                actor_user_id=actor_user_id,
                scope=scope,
                key=key,
                request_hash=request_hash,
                expires_at=expires_at,
                response_ref="",
            )
            self.records[record_key] = record
            return IdempotencyDecision(status="reserved", record=record)
        if existing.request_hash != request_hash:
            raise ApiError(
                code="idempotency_conflict",
                message="Idempotency key was reused with a different request.",
                status=409,
            )
        return IdempotencyDecision(status="replay", record=existing)

    async def mark_completed(self, *, record: IdempotencyKey, response_ref: str):
        record.response_ref = response_ref
        self.completed_response_refs.append(response_ref)
        return record


class FlowAuditService:
    def __init__(self) -> None:
        self.entries = []

    async def record(self, **kwargs):
        self.entries.append(kwargs)
