import asyncio
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from app.core.errors import ApiError
from app.modules.profiles.lactation_context import LactationContextService
from app.modules.profiles.models import (
    InfantProfile,
    LactationProfile,
    MaternalCurrentDeliveryInfant,
    MaternalProfile,
    UserProfile,
)
from app.modules.profiles.repository import (
    LactationInfantContext,
    LactationMotherContext,
)
from app.modules.records.models import GrowthRecord


def test_lactation_context_supports_multiple_babies_and_derives_shared_age() -> None:
    owner_user_id = uuid4()
    first_infant_id = uuid4()
    second_infant_id = uuid4()
    maternal = MaternalProfile(
        id=uuid4(),
        owner_user_id=owner_user_id,
        delivery_count=2,
        latest_delivery_method="cesarean",
        latest_delivery_date=date(2026, 5, 10),
        has_cesarean_history=True,
    )
    lactation = LactationProfile(
        id=uuid4(),
        owner_user_id=owner_user_id,
        current_feeding_mode="mixed_feeding",
    )
    first_infant = InfantProfile(
        id=first_infant_id,
        owner_user_id=owner_user_id,
        name="Baby A",
        sex_at_birth="female",
        birth_date=date(2026, 5, 10),
        birth_weight_kg=2.45,
        gestational_age_at_birth_days=258,
    )
    second_infant = InfantProfile(
        id=second_infant_id,
        owner_user_id=owner_user_id,
        name="Baby B",
        sex_at_birth="male",
        birth_date=date(2026, 5, 10),
        birth_weight_kg=2.3,
        gestational_age_at_birth_days=258,
    )
    profile_repository = FakeProfileRepository(
        user_profile=UserProfile(
            user_id=owner_user_id,
            preferred_name="Mai",
            age=32,
            estimated_due_date=date(2026, 5, 17),
        ),
        maternal_profile=maternal,
        lactation_profile=lactation,
        current_infants=[
            (
                MaternalCurrentDeliveryInfant(
                    maternal_profile_id=maternal.id,
                    infant_id=first_infant_id,
                    birth_order=1,
                ),
                first_infant,
            ),
            (
                MaternalCurrentDeliveryInfant(
                    maternal_profile_id=maternal.id,
                    infant_id=second_infant_id,
                    birth_order=2,
                ),
                second_infant,
            ),
        ],
        infants=[first_infant, second_infant],
    )
    records_service = FakeRecordsService(
        growth_by_infant={
            first_infant_id: [
                GrowthRecord(
                    owner_user_id=owner_user_id,
                    infant_id=first_infant_id,
                    measured_at=datetime(2026, 7, 20, 8, 30, tzinfo=timezone.utc),
                    weight_kg=5.1,
                    height_cm=57.5,
                    head_cm=38.2,
                )
            ],
            second_infant_id: [
                GrowthRecord(
                    owner_user_id=owner_user_id,
                    infant_id=second_infant_id,
                    measured_at=datetime(2026, 7, 21, 9, 0, tzinfo=timezone.utc),
                    weight_kg=4.9,
                    height_cm=56.8,
                    head_cm=37.9,
                )
            ],
        }
    )
    service = LactationContextService(
        profile_repository=profile_repository,
        records_service=records_service,
    )

    result = asyncio.run(service.read(owner_user_id=owner_user_id, as_of_date=date(2026, 7, 23)))

    assert result == {
        "as_of_date": "2026-07-23",
        "infant_scope": "current_delivery",
        "mother": {
            "preferred_name": "Mai",
            "age": 32,
            "estimated_due_date": None,
            "delivery_count": 2,
            "current_delivery_method": "cesarean",
            "actual_delivery_date": "2026-05-10",
            "has_cesarean_history": True,
            "postpartum_days": 74,
            "current_feeding_mode": "mixed_feeding",
        },
        "infants": [
            {
                "infant_id": str(first_infant_id),
                "name": "Baby A",
                "is_current_delivery": True,
                "birth_order": 1,
                "sex_at_birth": "female",
                "birth_date": "2026-05-10",
                "age_days": 74,
                "age_months": 2,
                "birth_weight_kg": 2.45,
                "gestational_age_at_birth": {
                    "total_days": 258,
                    "weeks": 36,
                    "days": 6,
                    "is_preterm": True,
                },
                "latest_measurement": {
                    "weight_kg": 5.1,
                    "height_cm": 57.5,
                    "head_circumference_cm": 38.2,
                    "measured_at": "2026-07-20T08:30:00+00:00",
                },
            },
            {
                "infant_id": str(second_infant_id),
                "name": "Baby B",
                "is_current_delivery": True,
                "birth_order": 2,
                "sex_at_birth": "male",
                "birth_date": "2026-05-10",
                "age_days": 74,
                "age_months": 2,
                "birth_weight_kg": 2.3,
                "gestational_age_at_birth": {
                    "total_days": 258,
                    "weeks": 36,
                    "days": 6,
                    "is_preterm": True,
                },
                "latest_measurement": {
                    "weight_kg": 4.9,
                    "height_cm": 56.8,
                    "head_circumference_cm": 37.9,
                    "measured_at": "2026-07-21T09:00:00+00:00",
                },
            },
        ],
        "missing_fields": [],
        "data_quality_issues": [],
    }
    assert records_service.queries == [
        {
            "owner_user_id": owner_user_id,
            "infant_ids": [first_infant_id, second_infant_id],
        }
    ]


def test_maternal_infant_profile_read_keeps_prenatal_due_date_when_delivery_is_unknown() -> None:
    owner_user_id = uuid4()
    service = LactationContextService(
        profile_repository=FakeProfileRepository(
            user_profile=UserProfile(
                user_id=owner_user_id,
                preferred_name="Not needed for milk analysis",
                estimated_due_date=date(2027, 1, 1),
            ),
            maternal_profile=None,
            lactation_profile=None,
            current_infants=[],
            infants=[],
        ),
        records_service=FakeRecordsService(growth_by_infant={}),
    )

    result = asyncio.run(service.read(owner_user_id=owner_user_id, as_of_date=date(2026, 7, 23)))

    assert result["mother"]["preferred_name"] == "Not needed for milk analysis"
    assert result["mother"]["estimated_due_date"] == "2027-01-01"
    assert result["mother"]["postpartum_days"] is None
    assert result["infants"] == []
    assert {"code": "mother_age_missing", "birth_order": None} in result["missing_fields"]
    assert {"code": "mother_actual_delivery_date_missing", "birth_order": None} in result["missing_fields"]
    assert {"code": "current_infant_profiles_missing", "birth_order": None} in result["missing_fields"]


def test_maternal_infant_profile_read_hides_due_date_when_baby_birth_date_exists() -> None:
    owner_user_id = uuid4()
    infant = InfantProfile(
        id=uuid4(),
        owner_user_id=owner_user_id,
        name="Baby",
        birth_date=date(2026, 5, 10),
    )
    service = LactationContextService(
        profile_repository=FakeProfileRepository(
            user_profile=UserProfile(
                user_id=owner_user_id,
                estimated_due_date=date(2026, 5, 17),
            ),
            maternal_profile=None,
            lactation_profile=None,
            current_infants=[],
            infants=[infant],
        ),
        records_service=FakeRecordsService(growth_by_infant={}),
    )

    result = asyncio.run(
        service.read(
            owner_user_id=owner_user_id,
            as_of_date=date(2026, 7, 23),
        )
    )

    assert result["mother"]["actual_delivery_date"] is None
    assert result["mother"]["estimated_due_date"] is None
    assert result["infants"][0]["birth_date"] == "2026-05-10"


def test_maternal_infant_profile_read_all_scope_returns_current_and_previous_babies() -> None:
    owner_user_id = uuid4()
    current_infant = InfantProfile(
        id=uuid4(),
        owner_user_id=owner_user_id,
        name="Current baby",
        sex_at_birth="female",
        birth_date=date(2026, 5, 10),
    )
    previous_infant = InfantProfile(
        id=uuid4(),
        owner_user_id=owner_user_id,
        name="Older child",
        sex_at_birth="male",
        birth_date=date(2022, 3, 2),
    )
    maternal = MaternalProfile(
        id=uuid4(),
        owner_user_id=owner_user_id,
        latest_delivery_date=date(2026, 5, 10),
    )
    service = LactationContextService(
        profile_repository=FakeProfileRepository(
            user_profile=UserProfile(user_id=owner_user_id),
            maternal_profile=maternal,
            lactation_profile=None,
            current_infants=[
                (
                    MaternalCurrentDeliveryInfant(
                        maternal_profile_id=maternal.id,
                        infant_id=current_infant.id,
                        birth_order=1,
                    ),
                    current_infant,
                )
            ],
            infants=[previous_infant, current_infant],
        ),
        records_service=FakeRecordsService(growth_by_infant={}),
    )

    result = asyncio.run(
        service.read(
            owner_user_id=owner_user_id,
            as_of_date=date(2026, 7, 23),
            infant_scope="all",
        )
    )

    assert result["infant_scope"] == "all"
    assert [
        (
            infant["infant_id"],
            infant["name"],
            infant["is_current_delivery"],
            infant["birth_order"],
        )
        for infant in result["infants"]
    ] == [
        (str(previous_infant.id), "Older child", False, None),
        (str(current_infant.id), "Current baby", True, 1),
    ]
    assert result["infants"][0]["age_days"] == 1604
    assert result["infants"][1]["age_days"] == 74


def test_lactation_context_reports_stable_data_quality_codes_with_infant_scope() -> None:
    owner_user_id = uuid4()
    infant = InfantProfile(
        id=uuid4(),
        owner_user_id=owner_user_id,
        name="Baby",
        birth_date=date(2026, 5, 11),
    )
    maternal = MaternalProfile(
        id=uuid4(),
        owner_user_id=owner_user_id,
        latest_delivery_date=date(2026, 5, 10),
    )
    service = LactationContextService(
        profile_repository=FakeProfileRepository(
            user_profile=None,
            maternal_profile=maternal,
            lactation_profile=None,
            current_infants=[
                (
                    MaternalCurrentDeliveryInfant(
                        maternal_profile_id=maternal.id,
                        infant_id=infant.id,
                        birth_order=1,
                    ),
                    infant,
                )
            ],
            infants=[infant],
        ),
        records_service=FakeRecordsService(growth_by_infant={}),
    )

    result = asyncio.run(service.read(owner_user_id=owner_user_id, as_of_date=date(2026, 7, 23)))

    assert result["data_quality_issues"] == [
        {
            "code": "infant_birth_date_mismatch",
            "birth_order": 1,
        }
    ]
    assert {
        "code": "infant_latest_measurement_missing",
        "birth_order": 1,
    } in result["missing_fields"]


def test_maternal_profile_update_rejects_cross_owner_infant() -> None:
    owner_user_id = uuid4()
    current_infant_id = uuid4()
    repository = FakeProfileRepository(
        user_profile=None,
        maternal_profile=None,
        lactation_profile=None,
        current_infants=[],
        infants=[],
    )
    service = LactationContextService(
        profile_repository=repository,
        records_service=FakeRecordsService(growth_by_infant={}),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.update_maternal_profile(
                owner_user_id=owner_user_id,
                values={
                    "current_infants": [
                        {
                            "infant_id": current_infant_id,
                            "birth_order": 1,
                        }
                    ],
                    "delivery_count": 1,
                },
            )
        )

    assert exc_info.value.code == "not_found"
    assert repository.upsert_values is None


def test_maternal_profile_update_rejects_duplicate_birth_order() -> None:
    owner_user_id = uuid4()
    first_infant_id = uuid4()
    second_infant_id = uuid4()
    repository = FakeProfileRepository(
        user_profile=None,
        maternal_profile=None,
        lactation_profile=None,
        current_infants=[],
        infants=[],
    )
    service = LactationContextService(
        profile_repository=repository,
        records_service=FakeRecordsService(growth_by_infant={}),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.update_maternal_profile(
                owner_user_id=owner_user_id,
                values={
                    "current_infants": [
                        {"infant_id": first_infant_id, "birth_order": 1},
                        {"infant_id": second_infant_id, "birth_order": 1},
                    ]
                },
            )
        )

    assert exc_info.value.code == "validation_failed"
    assert repository.upsert_values is None


def test_maternal_profile_update_splits_general_and_lactation_state() -> None:
    owner_user_id = uuid4()
    infant = InfantProfile(
        id=uuid4(),
        owner_user_id=owner_user_id,
        name="Baby",
        sex_at_birth="female",
        birth_date=date(2026, 5, 10),
    )
    repository = FakeProfileRepository(
        user_profile=None,
        maternal_profile=None,
        lactation_profile=None,
        current_infants=[],
        infants=[infant],
    )
    service = LactationContextService(
        profile_repository=repository,
        records_service=FakeRecordsService(growth_by_infant={}),
    )

    profile, current_infants = asyncio.run(
        service.update_maternal_profile(
            owner_user_id=owner_user_id,
            values={
                "current_infants": [{"infant_id": infant.id, "birth_order": 1}],
                "delivery_count": 2,
                "current_delivery_method": "cesarean",
                "actual_delivery_date": date(2026, 5, 10),
                "has_cesarean_history": True,
                "current_feeding_mode": "mixed_feeding",
            },
        )
    )

    assert repository.upsert_values == {
        "delivery_count": 2,
        "latest_delivery_method": "cesarean",
        "latest_delivery_date": date(2026, 5, 10),
        "has_cesarean_history": True,
    }
    assert repository.lactation_upsert_values == {"current_feeding_mode": "mixed_feeding"}
    assert profile.current_delivery_method == "cesarean"
    assert profile.current_feeding_mode == "mixed_feeding"
    assert current_infants == [{"infant_id": infant.id, "birth_order": 1}]


def test_maternal_profile_partial_update_checks_existing_current_infant() -> None:
    owner_user_id = uuid4()
    infant_id = uuid4()
    repository = FakeProfileRepository(
        user_profile=None,
        maternal_profile=(
            maternal := MaternalProfile(
                id=uuid4(),
                owner_user_id=owner_user_id,
                latest_delivery_date=date(2026, 5, 10),
            )
        ),
        lactation_profile=None,
        current_infants=[
            (
                MaternalCurrentDeliveryInfant(
                    maternal_profile_id=maternal.id,
                    infant_id=infant_id,
                    birth_order=1,
                ),
                InfantProfile(
                    id=infant_id,
                    owner_user_id=owner_user_id,
                    name="Baby",
                    birth_date=date(2026, 5, 10),
                ),
            )
        ],
        infants=[
            InfantProfile(
                id=infant_id,
                owner_user_id=owner_user_id,
                name="Baby",
                birth_date=date(2026, 5, 10),
            )
        ],
    )
    service = LactationContextService(
        profile_repository=repository,
        records_service=FakeRecordsService(growth_by_infant={}),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.update_maternal_profile(
                owner_user_id=owner_user_id,
                values={"actual_delivery_date": date(2026, 5, 11)},
            )
        )

    assert exc_info.value.code == "validation_failed"
    assert repository.upsert_values is None


def test_maternal_profile_update_accepts_matching_infant_birth_date_from_same_action() -> None:
    owner_user_id = uuid4()
    infant_id = uuid4()
    maternal = MaternalProfile(
        id=uuid4(),
        owner_user_id=owner_user_id,
        latest_delivery_date=date(2026, 5, 10),
    )
    infant = InfantProfile(
        id=infant_id,
        owner_user_id=owner_user_id,
        name="Baby",
        birth_date=date(2026, 5, 10),
    )
    repository = FakeProfileRepository(
        user_profile=None,
        maternal_profile=maternal,
        lactation_profile=None,
        current_infants=[
            (
                MaternalCurrentDeliveryInfant(
                    maternal_profile_id=maternal.id,
                    infant_id=infant_id,
                    birth_order=1,
                ),
                infant,
            )
        ],
        infants=[infant],
    )
    service = LactationContextService(
        profile_repository=repository,
        records_service=FakeRecordsService(growth_by_infant={}),
    )

    profile, _ = asyncio.run(
        service.update_maternal_profile(
            owner_user_id=owner_user_id,
            values={"actual_delivery_date": date(2026, 5, 11)},
            anticipated_infant_birth_dates={
                infant_id: date(2026, 5, 11),
            },
        )
    )

    assert profile.actual_delivery_date == date(2026, 5, 11)


class FakeProfileRepository:
    def __init__(
        self,
        *,
        user_profile: UserProfile | None,
        maternal_profile: MaternalProfile | None,
        lactation_profile: LactationProfile | None,
        current_infants: list[tuple[MaternalCurrentDeliveryInfant, InfantProfile]],
        infants: list[InfantProfile],
    ) -> None:
        self.user_profile = user_profile
        self.maternal_profile = maternal_profile
        self.lactation_profile = lactation_profile
        self.current_infants = current_infants
        self.infants = infants
        self.upsert_values = None
        self.lactation_upsert_values = None

    async def get_user_profile(self, *, user_id):
        return self.user_profile

    async def get_maternal_profile(self, *, owner_user_id):
        return self.maternal_profile

    async def get_lactation_profile(self, *, owner_user_id):
        return self.lactation_profile

    async def list_infants(self, *, owner_user_id):
        return self.infants

    async def list_current_delivery_infants(self, *, owner_user_id):
        return self.current_infants

    async def get_lactation_mother_context(self, *, owner_user_id):
        return LactationMotherContext(
            preferred_name=(self.user_profile.preferred_name if self.user_profile is not None else None),
            age=self.user_profile.age if self.user_profile is not None else None,
            estimated_due_date=(
                self.user_profile.estimated_due_date
                if self.user_profile is not None
                else None
            ),
            delivery_count=(self.maternal_profile.delivery_count if self.maternal_profile is not None else None),
            latest_delivery_method=(self.maternal_profile.latest_delivery_method if self.maternal_profile is not None else None),
            latest_delivery_date=(self.maternal_profile.latest_delivery_date if self.maternal_profile is not None else None),
            has_cesarean_history=(self.maternal_profile.has_cesarean_history if self.maternal_profile is not None else None),
            current_feeding_mode=(self.lactation_profile.current_feeding_mode if self.lactation_profile is not None else None),
        )

    async def list_current_delivery_infant_contexts(self, *, owner_user_id):
        return [
            (
                link.birth_order,
                _infant_context(infant),
            )
            for link, infant in self.current_infants
        ]

    async def list_infant_context_candidates(self, *, owner_user_id, limit):
        return [_infant_context(infant) for infant in self.infants[:limit]]

    async def list_all_infant_contexts(self, *, owner_user_id):
        return [_infant_context(infant) for infant in self.infants]

    async def get_infant_for_owner(self, *, infant_id, owner_user_id):
        return next(
            (infant for infant in self.infants if infant.id == infant_id and infant.owner_user_id == owner_user_id),
            None,
        )

    async def upsert_maternal_profile(self, *, owner_user_id, values):
        self.upsert_values = values
        self.maternal_profile = self.maternal_profile or MaternalProfile(owner_user_id=owner_user_id)
        for field, value in values.items():
            setattr(self.maternal_profile, field, value)
        return self.maternal_profile

    async def upsert_lactation_profile(self, *, owner_user_id, values):
        self.lactation_upsert_values = values
        self.lactation_profile = self.lactation_profile or LactationProfile(owner_user_id=owner_user_id)
        for field, value in values.items():
            setattr(self.lactation_profile, field, value)
        return self.lactation_profile

    async def replace_current_delivery_infants(self, *, profile, current_infants):
        self.current_infants = [
            (
                MaternalCurrentDeliveryInfant(
                    maternal_profile_id=profile.id,
                    infant_id=item["infant_id"],
                    birth_order=item["birth_order"],
                ),
                next(infant for infant in self.infants if infant.id == item["infant_id"]),
            )
            for item in current_infants
        ]


class FakeRecordsService:
    def __init__(
        self,
        *,
        growth_by_infant: dict,
    ) -> None:
        self.growth_by_infant = growth_by_infant
        self.queries = []

    async def list_latest_growth_by_infant_ids(
        self,
        *,
        owner_user_id,
        infant_ids,
    ):
        self.queries.append(
            {
                "owner_user_id": owner_user_id,
                "infant_ids": infant_ids,
            }
        )
        return {infant_id: records[0] for infant_id, records in self.growth_by_infant.items() if infant_id in infant_ids and records}


def _infant_context(infant: InfantProfile) -> LactationInfantContext:
    return LactationInfantContext(
        infant_id=infant.id,
        name=infant.name,
        sex_at_birth=infant.sex_at_birth,
        birth_date=infant.birth_date,
        birth_weight_kg=infant.birth_weight_kg,
        gestational_age_at_birth_days=infant.gestational_age_at_birth_days,
    )
