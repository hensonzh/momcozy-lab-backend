import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

from production_backend.app.modules.audit.models import IdempotencyKey
from production_backend.app.modules.profiles.models import InfantProfile, UserProfile
from production_backend.app.modules.profiles.service import ProfileService


def test_profile_service_updates_profile_and_records_audit() -> None:
    user_id = uuid4()
    profile = UserProfile(id=uuid4(), user_id=user_id)
    repository = FakeProfileRepository(profile=profile)
    audit_service = FakeAuditService()
    service = ProfileService(repository=repository, audit_service=audit_service)

    updated = asyncio.run(
        service.update_user_profile(
            user_id=user_id,
            values={"display_name": "Mia", "age": 32},
            request_id="req_profile",
        )
    )

    assert updated.display_name == "Mia"
    assert updated.age == 32
    assert audit_service.record_kwargs["action"] == "profiles.user.update"
    assert audit_service.record_kwargs["details"]["fields"] == ["age", "display_name"]


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
            infant_name="Baby",
            sex="female",
            birth_date=date(2026, 6, 1),
            request_id="req_infant",
            idempotency_key="idem-infant",
        )
    )

    assert infant.owner_user_id == owner_user_id
    assert infant.infant_name == "Baby"
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
        service.create_infant(owner_user_id=owner_user_id, infant_name="Baby", idempotency_key="idem-infant")
    )

    assert returned is existing
    assert repository.created_infant_kwargs == {}


class FakeProfileRepository:
    def __init__(self, *, profile=None, infant=None) -> None:
        self.profile = profile
        self.infant = infant
        self.created_infant_kwargs = {}

    async def get_user_profile(self, *, user_id: UUID):
        return self.profile

    async def upsert_user_profile(self, *, user_id: UUID, values: dict):
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
            infant_name=kwargs["infant_name"],
            sex=kwargs["sex"],
            birth_date=kwargs["birth_date"],
        )
        return self.infant


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
    infant_name: str = "Baby",
    sex: str = "female",
    birth_date: date | None = None,
) -> InfantProfile:
    return InfantProfile(
        id=infant_id or uuid4(),
        owner_user_id=owner_user_id,
        infant_name=infant_name,
        sex=sex,
        birth_date=birth_date,
        status="active",
    )
