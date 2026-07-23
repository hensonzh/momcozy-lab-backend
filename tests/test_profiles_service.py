import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.core.errors import ApiError
from app.modules.audit.models import IdempotencyKey
from app.modules.profiles.models import InfantProfile, UserProfile
from app.modules.profiles.service import ProfileService


def test_profile_service_updates_profile_and_records_audit() -> None:
    user_id = uuid4()
    profile = UserProfile(id=uuid4(), user_id=user_id)
    repository = FakeProfileRepository(profile=profile)
    audit_service = FakeAuditService()
    service = ProfileService(repository=repository, audit_service=audit_service)

    updated = asyncio.run(
        service.update_user_profile(
            user_id=user_id,
            values={
                "preferred_name": " Mia ",
                "age": 32,
                "estimated_due_date": date(2026, 9, 20),
            },
            request_id="req_profile",
        )
    )

    assert updated.preferred_name == "Mia"
    assert updated.age == 32
    assert updated.estimated_due_date == date(2026, 9, 20)
    assert audit_service.record_kwargs["action"] == "profiles.user.update"
    assert audit_service.record_kwargs["details"]["fields"] == [
        "age",
        "estimated_due_date",
        "preferred_name",
    ]


def test_profile_service_preserves_explicit_nulls_and_rejects_unknown_fields() -> None:
    user_id = uuid4()
    profile = UserProfile(
        id=uuid4(),
        user_id=user_id,
        preferred_name="Mia",
        age=32,
        estimated_due_date=date(2026, 9, 20),
    )
    service = ProfileService(repository=FakeProfileRepository(profile=profile))

    updated = asyncio.run(
        service.update_user_profile(
            user_id=user_id,
            values={"preferred_name": None, "estimated_due_date": None},
        )
    )

    assert updated.preferred_name is None
    assert updated.estimated_due_date is None
    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.update_user_profile(
                user_id=user_id,
                values={"display_name": "legacy"},
            )
        )
    assert exc_info.value.code == "validation_failed"


def test_profile_service_updates_user_and_owned_infant_as_one_profile_change() -> None:
    user_id = uuid4()
    profile = UserProfile(id=uuid4(), user_id=user_id)
    infant = _infant(owner_user_id=user_id, name="Baby")
    repository = FakeProfileRepository(profile=profile, infant=infant)
    audit_service = FakeAuditService()
    service = ProfileService(repository=repository, audit_service=audit_service)

    updated_profile, updated_infants = asyncio.run(
        service.update_profile(
            user_id=user_id,
            user_values={"preferred_name": " Mia "},
            infant_updates=[
                {
                    "infant_id": infant.id,
                    "values": {
                        "name": " Nori ",
                        "sex_at_birth": "female",
                        "birth_date": date(2026, 1, 10),
                    },
                }
            ],
            request_id="req_profile_bundle",
        )
    )

    assert updated_profile is profile
    assert profile.preferred_name == "Mia"
    assert updated_infants == [infant]
    assert infant.name == "Nori"
    assert infant.birth_date == date(2026, 1, 10)
    assert audit_service.record_kwargs == {
        "actor_user_id": user_id,
        "action": "profiles.update",
        "resource_type": "profile",
        "resource_id": str(user_id),
        "request_id": "req_profile_bundle",
        "details": {
            "user_fields": ["preferred_name"],
            "infants": [
                {
                    "infant_id": str(infant.id),
                    "fields": ["birth_date", "name", "sex_at_birth"],
                }
            ],
        },
    }


def test_profile_service_resolves_all_infants_before_mutating_user_profile() -> None:
    user_id = uuid4()
    repository = FakeProfileRepository(profile=UserProfile(id=uuid4(), user_id=user_id))
    service = ProfileService(repository=repository)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.update_profile(
                user_id=user_id,
                user_values={"preferred_name": "Mia"},
                infant_updates=[{"infant_id": uuid4(), "values": {"name": "Nori"}}],
            )
        )

    assert exc_info.value.code == "not_found"
    assert repository.upsert_user_profile_kwargs == {}


def test_profile_service_creates_infant_with_idempotency_and_audit() -> None:
    owner_user_id = uuid4()
    repository = FakeProfileRepository()
    idempotency_service = FakeIdempotencyService(status="reserved")
    audit_service = FakeAuditService()
    service = ProfileService(
        repository=repository,
        audit_service=audit_service,
        idempotency_service=idempotency_service,
    )

    infant = asyncio.run(
        service.create_infant(
            owner_user_id=owner_user_id,
            name="Baby",
            sex_at_birth="female",
            birth_date=date(2026, 6, 1),
            request_id="req_infant",
            idempotency_key="idem-infant",
        )
    )

    assert infant.owner_user_id == owner_user_id
    assert infant.name == "Baby"
    assert idempotency_service.reserve_kwargs["scope"] == "profiles.infants.create"
    assert idempotency_service.completed_response_ref == str(infant.id)
    assert audit_service.record_kwargs["action"] == "profiles.infant.create"


def test_profile_service_replays_completed_infant_create() -> None:
    owner_user_id = uuid4()
    infant_id = uuid4()
    existing = _infant(owner_user_id=owner_user_id, infant_id=infant_id)
    repository = FakeProfileRepository(infant=existing)
    service = ProfileService(
        repository=repository,
        idempotency_service=FakeIdempotencyService(status="replay", response_ref=str(infant_id)),
    )

    returned = asyncio.run(
        service.create_infant(owner_user_id=owner_user_id, name="Baby", idempotency_key="idem-infant")
    )

    assert returned is existing
    assert repository.created_infant_kwargs == {}


class FakeProfileRepository:
    def __init__(self, *, profile=None, infant=None) -> None:
        self.profile = profile
        self.infant = infant
        self.created_infant_kwargs = {}
        self.upsert_user_profile_kwargs = {}

    async def get_user_profile(self, *, user_id: UUID):
        return self.profile

    async def upsert_user_profile(self, *, user_id: UUID, values: dict):
        self.upsert_user_profile_kwargs = {"user_id": user_id, "values": values}
        profile = self.profile or UserProfile(id=uuid4(), user_id=user_id)
        for field, value in values.items():
            setattr(profile, field, value)
        self.profile = profile
        return profile

    async def list_infants(self, *, owner_user_id: UUID):
        return [self.infant] if self.infant is not None else []

    async def get_infant_for_owner(self, *, infant_id: UUID, owner_user_id: UUID):
        return self.infant

    async def create_infant(self, **kwargs):
        self.created_infant_kwargs = kwargs
        self.infant = _infant(
            owner_user_id=kwargs["owner_user_id"],
            name=kwargs["name"],
            sex_at_birth=kwargs["sex_at_birth"],
            birth_date=kwargs["birth_date"],
        )
        return self.infant

    async def update_infant(self, *, infant: InfantProfile, values: dict):
        for field, value in values.items():
            setattr(infant, field, value)
        return infant


class FakeIdempotencyService:
    def __init__(self, *, status: str, response_ref: str = "") -> None:
        self.status = status
        self.reserve_kwargs = {}
        self.completed_response_ref = ""
        self.record = IdempotencyKey(
            actor_user_id=uuid4(),
            scope="profiles.infants.create",
            key="idem-infant",
            request_hash="hash",
            response_ref=response_ref,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

    async def reserve(self, **kwargs):
        self.reserve_kwargs = kwargs
        self.record.actor_user_id = kwargs["actor_user_id"]
        return FakeIdempotencyDecision(status=self.status, record=self.record)

    async def mark_completed(self, *, record, response_ref: str):
        self.completed_response_ref = response_ref
        record.response_ref = response_ref
        return record


class FakeIdempotencyDecision:
    def __init__(self, *, status: str, record) -> None:
        self.status = status
        self.record = record


class FakeAuditService:
    def __init__(self) -> None:
        self.record_kwargs = {}

    async def record(self, **kwargs):
        self.record_kwargs = kwargs
        return None


def _infant(
    *,
    owner_user_id: UUID,
    infant_id: UUID | None = None,
    name: str = "Baby",
    sex_at_birth: str = "female",
    birth_date: date | None = None,
) -> InfantProfile:
    return InfantProfile(
        id=infant_id or uuid4(),
        owner_user_id=owner_user_id,
        name=name,
        sex_at_birth=sex_at_birth,
        birth_date=birth_date,
    )
