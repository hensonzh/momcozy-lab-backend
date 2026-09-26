
from app.modules.baby.profile_models import BabyProfile
import asyncio
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from app.core.errors import ApiError
from app.modules.profiles.lactation_context import LactationContextService
from app.modules.profiles.me_models import MePreferences
from app.modules.profiles.models import (
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
    first_infant = BabyProfile(
        id=first_infant_id,
        owner_user_id=owner_user_id,
        name="Baby A",
        sex="female",
        birth_date=date(2026, 5, 10),
    )
    second_infant = BabyProfile(
        id=second_infant_id,
        owner_user_id=owner_user_id,
        name="Baby B",
        sex="male",
        birth_date=date(2026, 5, 10),
    )
    profile_repository = FakeProfileRepository(
        user_profile=UserProfile(
            user_id=owner_user_id,
            preferred_name="Mai",
            age=32,
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
            "delivery_count": 2,
            "current_delivery_method": "cesarean",
            "actual_delivery_date": "2026-05-10",
            "has_cesarean_history": True,
            "postpartum_days": 74,
            "current_feeding_mode": "mixed_feeding",
            "personal_context": {
                "baby_count": None, "gestation_weeks": None, "gestation_days": None,
                "feeding_methods": None, "feeding_preference": None, "caregivers": None,
                "return_to_work_date": None, "additional_context": None,
                "active_concerns": [], "active_concern_count": 0,
            },
        },
        "infants": [
            {
                "infant_id": str(first_infant_id),
                "name": "Baby A",
                "is_current_delivery": True,
                "birth_order": 1,
                "sex": "female",
                "feeding_mode": "unknown",
                "birth_date": "2026-05-10",
                "age_days": 74,
                "age_months": 2,
                "latest_measurement": {
                    "weight_kg": 5.1,
                    "height_cm": 57.5,
                    "head_circumference_cm": 38.2,
                    "measured_at": "2026-07-20T08:30:00Z",
                    "recorded_on": "2026-07-20", "source": "growth_records",
                },
            },
            {
                "infant_id": str(second_infant_id),
                "name": "Baby B",
                "is_current_delivery": True,
                "birth_order": 2,
                "sex": "male",
                "feeding_mode": "unknown",
                "birth_date": "2026-05-10",
                "age_days": 74,
                "age_months": 2,
                "latest_measurement": {
                    "weight_kg": 4.9,
                    "height_cm": 56.8,
                    "head_circumference_cm": 37.9,
                    "measured_at": "2026-07-21T09:00:00Z",
                    "recorded_on": "2026-07-21", "source": "growth_records",
                },
            },
        ],
        "missing_fields": [],
        "data_quality_issues": [],
    }
    assert result["mother"]["personal_context"] == {
        "baby_count": None, "gestation_weeks": None, "gestation_days": None,
        "feeding_methods": None, "feeding_preference": None, "caregivers": None,
        "return_to_work_date": None, "additional_context": None,
        "active_concerns": [], "active_concern_count": 0,
    }
    assert records_service.queries == [
        {
            "owner_user_id": owner_user_id,
            "infant_ids": [first_infant_id, second_infant_id],
            "as_of_date": date(2026, 7, 23),
            "timezone": "UTC",
        }
    ]


def test_lactation_context_projects_only_bounded_active_personal_profile() -> None:
    owner = uuid4()
    preferences = MePreferences(
        owner_user_id=owner,
        profile={
            "baby_count": 2, "gestation_weeks": 37, "gestation_days": 4,
            "feeding_methods": ["direct", "expressed"],
            "feeding_preference": "mixed", "caregivers": ["partner"],
            "return_to_work_date": "2026-10-12",
            "additional_context": "I need rest.",
            "unrelated_field": "must never be projected",
        },
        concerns=[
            {"id": str(uuid4()), "issues": ["supply"], "note": "Current concern", "ended": False},
            {"id": str(uuid4()), "issues": ["work"], "note": "Old concern", "ended": True},
        ],
        record_order=["feed", "energy", "sleep", "mood"],
    )
    service = LactationContextService(
        profile_repository=FakeProfileRepository(
            user_profile=None, maternal_profile=None, lactation_profile=None,
            current_infants=[], infants=[], me_preferences=preferences,
        ),
        records_service=FakeRecordsService(growth_by_infant={}),
    )
    result = asyncio.run(service.read(owner_user_id=owner, as_of_date=date(2026, 9, 24)))
    personal = result["mother"]["personal_context"]
    assert personal == {
        "baby_count": 2, "gestation_weeks": 37, "gestation_days": 4,
        "feeding_methods": ["direct", "expressed"], "feeding_preference": "mixed",
        "caregivers": ["partner"], "return_to_work_date": "2026-10-12",
        "additional_context": "I need rest.",
        "active_concerns": [{"issues": ["supply"], "note": "Current concern"}],
        "active_concern_count": 1,
    }
    assert "Old concern" not in str(result)
    assert "must never be projected" not in str(result)


def test_maternal_infant_profile_read_does_not_infer_unlinked_single_baby_as_current() -> None:
    owner_user_id = uuid4()
    infant = BabyProfile(
        id=uuid4(),
        owner_user_id=owner_user_id,
        name="Older child",
        birth_date=date(2022, 3, 2),
    )
    service = LactationContextService(
        profile_repository=FakeProfileRepository(
            user_profile=UserProfile(
                user_id=owner_user_id,
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
    assert result["infants"] == []
    assert result["data_quality_issues"] == [
        {"code": "current_infants_not_selected", "birth_order": None},
    ]


def test_maternal_infant_profile_read_all_scope_returns_current_and_previous_babies() -> None:
    owner_user_id = uuid4()
    current_infant = BabyProfile(
        id=uuid4(),
        owner_user_id=owner_user_id,
        name="Current baby",
        sex="female",
        birth_date=date(2026, 5, 10),
    )
    previous_infant = BabyProfile(
        id=uuid4(),
        owner_user_id=owner_user_id,
        name="Older child",
        sex="male",
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


def test_maternal_infant_profile_read_chunks_latest_growth_queries_for_more_than_ten_babies() -> None:
    owner_user_id = uuid4()
    infants = [
        BabyProfile(
            id=uuid4(),
            owner_user_id=owner_user_id,
            name=f"Baby {index}",
        )
        for index in range(11)
    ]
    records_service = FakeRecordsService(growth_by_infant={})
    service = LactationContextService(
        profile_repository=FakeProfileRepository(
            user_profile=None,
            maternal_profile=None,
            lactation_profile=None,
            current_infants=[],
            infants=infants,
        ),
        records_service=records_service,
    )

    result = asyncio.run(
        service.read(
            owner_user_id=owner_user_id,
            as_of_date=date(2026, 7, 23),
            infant_scope="all",
        )
    )

    assert len(result["infants"]) == 11
    assert [len(query["infant_ids"]) for query in records_service.queries] == [10, 1]


def test_lactation_context_reports_stable_data_quality_codes_with_infant_scope() -> None:
    owner_user_id = uuid4()
    infant = BabyProfile(
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
    infant = BabyProfile(
        id=uuid4(),
        owner_user_id=owner_user_id,
        name="Baby",
        sex="female",
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


def test_maternal_profile_update_does_not_infer_prior_history_from_current_delivery() -> None:
    owner_user_id = uuid4()
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

    profile, _ = asyncio.run(
        service.update_maternal_profile(
            owner_user_id=owner_user_id,
            values={"current_delivery_method": "cesarean"},
        )
    )

    assert repository.upsert_values == {"latest_delivery_method": "cesarean"}
    assert profile.has_cesarean_history is None


def test_maternal_profile_update_allows_no_prior_cesarean_with_current_cesarean() -> None:
    owner_user_id = uuid4()
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

    profile, _ = asyncio.run(
        service.update_maternal_profile(
            owner_user_id=owner_user_id,
            values={
                "delivery_count": 2,
                "current_delivery_method": "cesarean",
                "has_cesarean_history": False,
            },
        )
    )

    assert profile.has_cesarean_history is False
    assert repository.upsert_values["latest_delivery_method"] == "cesarean"


def test_first_delivery_has_no_prior_cesarean_even_when_current_delivery_is_cesarean() -> None:
    owner_user_id = uuid4()
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

    profile, _ = asyncio.run(
        service.update_maternal_profile(
            owner_user_id=owner_user_id,
            values={"delivery_count": 1, "current_delivery_method": "cesarean"},
        )
    )
    assert profile.has_cesarean_history is False
    assert repository.upsert_values["has_cesarean_history"] is False

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.update_maternal_profile(
                owner_user_id=owner_user_id,
                values={"delivery_count": 1, "has_cesarean_history": True},
            )
        )
    assert exc_info.value.code == "validation_failed"


def test_maternal_profile_update_uses_trusted_reference_date_for_delivery_validation() -> None:
    owner_user_id = uuid4()
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

    profile, _ = asyncio.run(
        service.update_maternal_profile(
            owner_user_id=owner_user_id,
            values={"actual_delivery_date": date(2099, 1, 1)},
            reference_date=date(2099, 1, 1),
        )
    )

    assert profile.actual_delivery_date == date(2099, 1, 1)


def test_maternal_profile_update_rejects_stale_current_infant_replacement() -> None:
    owner_user_id = uuid4()
    infant = BabyProfile(
        id=uuid4(),
        owner_user_id=owner_user_id,
        name="Baby",
    )
    maternal = MaternalProfile(
        id=uuid4(),
        owner_user_id=owner_user_id,
    )
    repository = FakeProfileRepository(
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
    )
    service = LactationContextService(
        profile_repository=repository,
        records_service=FakeRecordsService(growth_by_infant={}),
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            service.update_maternal_profile(
                owner_user_id=owner_user_id,
                values={"current_infants": []},
                expected_current_infants=[],
            )
        )

    assert exc_info.value.code == "version_conflict"
    assert repository.current_infants


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
                BabyProfile(
                    id=infant_id,
                    owner_user_id=owner_user_id,
                    name="Baby",
                    birth_date=date(2026, 5, 10),
                ),
            )
        ],
        infants=[
            BabyProfile(
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
    infant = BabyProfile(
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
        current_infants: list[tuple[MaternalCurrentDeliveryInfant, BabyProfile]],
        infants: list[BabyProfile],
        me_preferences: MePreferences | None = None,
    ) -> None:
        self.me_preferences = me_preferences
        self.user_profile = user_profile
        self.maternal_profile = maternal_profile
        self.lactation_profile = lactation_profile
        self.current_infants = current_infants
        self.infants = infants
        self.upsert_values = None
        self.lactation_upsert_values = None
        self.profile_owner_locks = 0

    async def lock_profile_owner(self, *, owner_user_id):
        self.profile_owner_locks += 1

    async def get_user_profile(self, *, user_id):
        return self.user_profile

    async def get_me_preferences(self, *, owner_user_id):
        return self.me_preferences

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
        as_of_date=None,
        timezone="UTC",
    ):
        self.queries.append(
            {
                "owner_user_id": owner_user_id,
                "infant_ids": infant_ids,
                "as_of_date": as_of_date,
                "timezone": timezone,
            }
        )
        return {infant_id: records[0] for infant_id, records in self.growth_by_infant.items() if infant_id in infant_ids and records}


def _infant_context(infant: BabyProfile) -> LactationInfantContext:
    return LactationInfantContext(
        infant_id=infant.id,
        name=infant.name,
        sex=infant.sex or "unspecified",
        birth_date=infant.birth_date,
    )


def test_latest_growth_uses_one_app_record_without_backfilling_other_metrics() -> None:
    from app.modules.baby.models import BabyRecord
    from app.modules.profiles.lactation_context import _choose_latest_growth, _latest_measurement

    owner, baby = uuid4(), uuid4()
    old = GrowthRecord(owner_user_id=owner, infant_id=baby,
        measured_at=datetime(2026, 8, 1, 12, tzinfo=timezone.utc), weight_kg=4.0, height_cm=55.0)
    new = BabyRecord(owner_user_id=owner, baby_id=baby, kind="growth", recorded_on=date(2026, 8, 2),
        data={"metric": "weight", "value": 4.2, "timezone": "Asia/Shanghai"})
    snapshot = _latest_measurement(_choose_latest_growth(old, new))
    assert snapshot == {"weight_kg": 4.2, "height_cm": None, "head_circumference_cm": None,
        "recorded_on": date(2026, 8, 2), "measured_at": None, "source": "baby_records"}
    assert _latest_measurement(_choose_latest_growth(old, None))["source"] == "growth_records"


def test_latest_growth_compares_and_reports_legacy_date_in_client_timezone() -> None:
    from app.modules.baby.models import BabyRecord
    from app.modules.profiles.lactation_context import _choose_latest_growth, _latest_measurement
    from zoneinfo import ZoneInfo

    owner, baby = uuid4(), uuid4()
    zone = ZoneInfo("Asia/Shanghai")
    legacy = GrowthRecord(owner_user_id=owner, infant_id=baby,
        measured_at=datetime(2026, 9, 24, 17, tzinfo=timezone.utc), weight_kg=4.0)
    app = BabyRecord(owner_user_id=owner, baby_id=baby, kind="growth", recorded_on=date(2026, 9, 24),
        data={"metric": "weight", "value": 4.2})
    assert _choose_latest_growth(legacy, app, zone=zone) is legacy
    assert _latest_measurement(legacy, zone=zone)["recorded_on"] == date(2026, 9, 25)

    legacy.measured_at = datetime(2026, 9, 24, 15, 59, tzinfo=timezone.utc)
    assert _choose_latest_growth(legacy, app, zone=zone) is app
    assert _latest_measurement(legacy, zone=zone)["recorded_on"] == date(2026, 9, 24)


def test_profile_snapshot_applies_local_date_independently_to_two_babies() -> None:
    from app.modules.baby.models import BabyRecord

    owner, first, second = uuid4(), uuid4(), uuid4()
    maternal = MaternalProfile(id=uuid4(), owner_user_id=owner, latest_delivery_date=date(2026, 8, 1))
    babies = [BabyProfile(id=first, owner_user_id=owner, name="A"),
              BabyProfile(id=second, owner_user_id=owner, name="B")]
    links = [(MaternalCurrentDeliveryInfant(maternal_profile_id=maternal.id,
              infant_id=baby.id, birth_order=index), baby)
             for index, baby in enumerate(babies, start=1)]
    legacy_first = GrowthRecord(owner_user_id=owner, infant_id=first,
        measured_at=datetime(2026, 9, 24, 15, 59, tzinfo=timezone.utc), weight_kg=4.0)
    legacy_second = GrowthRecord(owner_user_id=owner, infant_id=second,
        measured_at=datetime(2026, 9, 23, 16, tzinfo=timezone.utc), weight_kg=5.0)
    app_first = BabyRecord(owner_user_id=owner, baby_id=first, kind="growth",
        recorded_on=date(2026, 9, 23), data={"metric": "weight", "value": 3.9})
    app_second = BabyRecord(owner_user_id=owner, baby_id=second, kind="growth",
        recorded_on=date(2026, 9, 24), data={"metric": "weight", "value": 5.1})

    class FakeAppGrowth:
        async def list_latest_growth_by_infant_ids(self, *, owner_user_id, infant_ids, as_of_date=None):
            assert (owner_user_id, infant_ids, as_of_date) == (owner, [first, second], date(2026, 9, 24))
            return {first: app_first, second: app_second}

    records = FakeRecordsService(growth_by_infant={first: [legacy_first], second: [legacy_second]})
    service = LactationContextService(
        profile_repository=FakeProfileRepository(user_profile=None, maternal_profile=maternal,
            lactation_profile=None, current_infants=links, infants=babies),
        records_service=records, baby_records_repository=FakeAppGrowth())
    result = asyncio.run(service.read(owner_user_id=owner, as_of_date=date(2026, 9, 24),
        timezone="Asia/Shanghai"))

    assert records.queries[0]["timezone"] == "Asia/Shanghai"
    first_measurement, second_measurement = (baby["latest_measurement"] for baby in result["infants"])
    assert first_measurement["source"] == "growth_records"
    assert first_measurement["recorded_on"] == "2026-09-24"
    assert first_measurement["measured_at"] == "2026-09-24T15:59:00Z"
    assert second_measurement["source"] == "baby_records"
    assert second_measurement["weight_kg"] == 5.1
    assert second_measurement["recorded_on"] == "2026-09-24"


def test_current_run_profile_prefers_latest_app_growth_without_cross_baby_merge() -> None:
    from app.modules.baby.models import BabyRecord

    owner, first, second = uuid4(), uuid4(), uuid4()
    maternal = MaternalProfile(id=uuid4(), owner_user_id=owner, latest_delivery_date=date(2026, 8, 1))
    babies = [
        BabyProfile(id=first, owner_user_id=owner, name="A", birth_date=date(2026, 8, 1)),
        BabyProfile(id=second, owner_user_id=owner, name="B", birth_date=date(2026, 8, 1)),
    ]
    links = [
        (MaternalCurrentDeliveryInfant(maternal_profile_id=maternal.id, infant_id=baby.id, birth_order=index), baby)
        for index, baby in enumerate(babies, start=1)
    ]
    old_growth = GrowthRecord(owner_user_id=owner, infant_id=first,
        measured_at=datetime(2026, 9, 20, 12, tzinfo=timezone.utc), weight_kg=4.0, height_cm=54.0)
    latest = BabyRecord(owner_user_id=owner, baby_id=first, kind="growth", recorded_on=date(2026, 9, 23),
        data={"metric": "weight", "value": 4.3, "timezone": "Asia/Shanghai"})

    class FakeBabyGrowth:
        async def list_latest_growth_by_infant_ids(self, *, owner_user_id, infant_ids, as_of_date=None):
            assert owner_user_id == owner
            assert infant_ids == [first, second]
            assert as_of_date == date(2026, 9, 24)
            return {first: latest}

    service = LactationContextService(
        profile_repository=FakeProfileRepository(user_profile=None, maternal_profile=maternal,
            lactation_profile=None, current_infants=links, infants=babies),
        records_service=FakeRecordsService(growth_by_infant={first: [old_growth]}),
        baby_records_repository=FakeBabyGrowth(),
    )
    result = asyncio.run(service.read(owner_user_id=owner, as_of_date=date(2026, 9, 24)))
    first_measurement, second_measurement = (baby["latest_measurement"] for baby in result["infants"])
    assert first_measurement == {"weight_kg": 4.3, "height_cm": None, "head_circumference_cm": None,
        "recorded_on": "2026-09-23", "measured_at": None, "source": "baby_records"}
    assert second_measurement is None
    assert {item["code"] for item in result["missing_fields"]} >= {"infant_latest_measurement_missing"}


def test_personal_context_ignores_corrupt_fields_and_bounds_active_concerns() -> None:
    from app.modules.profiles.lactation_context import _personal_context

    preferences = MePreferences(owner_user_id=uuid4(), profile={
        "baby_count": 2, "additional_context": "x" * 501, "unrelated_secret": "never include me",
    }, concerns=[
        {"id": str(uuid4()), "issues": ["supply"], "note": "needs help", "ended": False}
        for _ in range(12)
    ], record_order=[])
    context = _personal_context(preferences)
    assert context["baby_count"] == 2
    assert context["additional_context"] is None
    assert "unrelated_secret" not in context
    assert len(context["active_concerns"]) == 10
    assert context["active_concern_count"] == 12
