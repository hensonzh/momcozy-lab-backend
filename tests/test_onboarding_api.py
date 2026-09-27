import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.dependencies import require_current_user
from app.core.errors import ApiError
from app.core.settings import Settings
from app.factory import create_app
from app.infrastructure.db.session import get_session
from app.modules.auth import CurrentUser
from app.modules.baby.profile_models import BabyProfile
from app.modules.onboarding.models import OnboardingConfirmation
from app.modules.onboarding import schemas as onboarding_schemas
from app.modules.onboarding.router import get_onboarding_service
from app.modules.onboarding.schemas import OnboardingProfileInput, OnboardingStateOutput
from app.modules.onboarding.service import OnboardingService
from app.modules.profiles.models import LactationProfile, MaternalCurrentDeliveryInfant, MaternalProfile, UserProfile
from app.modules.profiles.me_models import MePreferences
from product_database import database, postgres


def payload(**changes):
    values = {
        "stage": "postpartum", "display_name": "Mia", "age": 32,
        "delivery_date": (date.today() - timedelta(days=3)).isoformat(),
        "delivery_count": 1, "has_cesarean_history": False, "delivery_type": "cesarean",
        "gestation_weeks": 39, "gestation_days": 2,
        "feeding_methods": ["direct", "formula"],
        "infant_count": 1, "infants": [{"nickname": "", "sex": None}],
    }
    values.update(changes)
    return values


def test_onboarding_requires_valid_gestational_age_and_feeding_methods():
    data = OnboardingProfileInput.model_validate(payload())
    assert (data.gestation_weeks, data.gestation_days) == (39, 2)
    assert data.feeding_methods == ["direct", "formula"]
    assert OnboardingProfileInput.model_validate(payload(feeding_methods=["formula", "direct"])).feeding_methods == ["direct", "formula"]
    for field in ("gestation_weeks", "gestation_days", "feeding_methods"):
        missing = payload()
        del missing[field]
        with pytest.raises(ValidationError):
            OnboardingProfileInput.model_validate(missing)


def test_delivery_date_is_checked_against_device_calendar_day(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 26, 20, tzinfo=timezone.utc).astimezone(tz or timezone.utc)

    monkeypatch.setattr(onboarding_schemas, "datetime", FixedDateTime)
    tomorrow_in_shanghai = OnboardingProfileInput.model_validate(payload(
        delivery_date="2026-09-27", client_timezone_offset_minutes=480,
    ))
    assert tomorrow_in_shanghai.delivery_date.isoformat() == "2026-09-27"
    assert "client_timezone_offset_minutes" not in tomorrow_in_shanghai.model_dump(mode="json")
    for value in [
        payload(delivery_date="2026-09-28", client_timezone_offset_minutes=480),
        payload(delivery_date="2026-09-27", client_timezone_offset_minutes=-300),
        payload(delivery_date="2026-09-27", client_timezone_offset_minutes=900),
    ]:
        with pytest.raises(ValidationError):
            OnboardingProfileInput.model_validate(value)


def test_onboarding_requires_authenticated_owner():
    client = TestClient(create_app(Settings(app_env="test")))
    assert client.get("/v1/onboarding/me").status_code == 401
    assert client.put("/v1/onboarding/me/profile", json=payload()).status_code == 401


def test_onboarding_routes_use_current_user_scope_and_no_store():
    owner = uuid4()

    class FakeService:
        async def read(self, user_id):
            assert user_id == owner
            return OnboardingStateOutput(status="required", profile_confirmed=False)

        async def confirm(self, user_id, data, request_id):
            assert user_id == owner and data.delivery_count == 1
            assert request_id == "req_onboarding"
            return OnboardingStateOutput(
                status="completed", profile_confirmed=True, primary_infant_id=uuid4()
            )

    app = create_app(Settings(app_env="test"))

    async def fake_current_user():
        return CurrentUser(
            user_id=owner, subject=str(owner), session_id="session", token_id="token",
            roles=frozenset({"user"}), permissions=frozenset(),
        )

    app.dependency_overrides[require_current_user] = fake_current_user
    app.dependency_overrides[get_onboarding_service] = FakeService
    client = TestClient(app)
    state = client.get("/v1/onboarding/me")
    assert state.status_code == 200 and state.json()["profile_confirmed"] is False
    assert state.headers["cache-control"] == "private, no-store"
    confirmed = client.put(
        "/v1/onboarding/me/profile", json=payload(), headers={"X-Request-ID": "req_onboarding"}
    )
    assert confirmed.status_code == 200 and confirmed.json()["profile_confirmed"] is True
    assert confirmed.headers["cache-control"] == "private, no-store"


@postgres
def test_authenticated_onboarding_http_flow_persists_and_replays_once():
    async def run():
        async with database() as (sessions, _booking, _clock, _provider, owners, _episodes):
            app = create_app(Settings(app_env="test"))

            async def fake_current_user():
                return CurrentUser(
                    user_id=owners[0], subject=str(owners[0]), session_id="session",
                    token_id="token", roles=frozenset({"user"}), permissions=frozenset(),
                )

            async def isolated_session():
                async with sessions() as session:
                    try:
                        yield session
                        await session.commit()
                    except Exception:
                        await session.rollback()
                        raise

            app.dependency_overrides[require_current_user] = fake_current_user
            app.dependency_overrides[get_session] = isolated_session
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                assert (await client.get("/v1/onboarding/me")).json() == {
                    "status": "required", "profile_confirmed": False, "primary_infant_id": None,
                }
                first = await client.put("/v1/onboarding/me/profile", json=payload(client_timezone_offset_minutes=480))
                assert first.status_code == 200 and first.json()["profile_confirmed"] is True
                assert (await client.put("/v1/onboarding/me/profile", json=payload(client_timezone_offset_minutes=-300))).json() == first.json()
                assert (await client.get("/v1/onboarding/me")).json() == first.json()
                me = await client.get(
                    "/v1/profile/me-experience",
                    params={"start": "2026-09-20T00:00:00Z", "end": "2026-09-21T00:00:00Z"},
                )
                assert me.status_code == 200
                assert me.json()["profile"]["baby_count"] == 1
                assert me.json()["profile"]["gestation_weeks"] == 39
                assert me.json()["profile"]["gestation_days"] == 2
                assert me.json()["profile"]["feeding_methods"] == ["direct", "formula"]
                updated = await client.patch("/v1/profile/me-experience/profile", json={"feeding_methods": ["direct", "expressed"]})
                assert updated.status_code == 200
                assert updated.json()["feeding_methods"] == ["direct", "expressed"]
                async with sessions() as check:
                    lactation = await check.scalar(select(LactationProfile).where(LactationProfile.owner_user_id == owners[0]))
                    baby = await check.scalar(select(BabyProfile).where(BabyProfile.owner_user_id == owners[0]))
                    assert lactation.current_feeding_mode == "exclusive_breastfeeding"
                    assert baby.feeding_mode == "exclusive_breastfeeding"
                unsure = await client.patch("/v1/profile/me-experience/profile", json={"feeding_methods": ["unknown"]})
                assert unsure.status_code == 200
                assert unsure.json()["feeding_methods"] == ["unknown"]
                async with sessions() as check:
                    lactation = await check.scalar(select(LactationProfile).where(LactationProfile.owner_user_id == owners[0]))
                    baby = await check.scalar(select(BabyProfile).where(BabyProfile.owner_user_id == owners[0]))
                    assert lactation.current_feeding_mode == "unknown"
                    assert baby.feeding_mode == "unknown"
                assert (await client.put("/v1/onboarding/me/profile", json=payload(display_name="Other"))).status_code == 409
            async with sessions.begin() as session:
                assert await session.scalar(select(func.count(BabyProfile.id)).where(BabyProfile.owner_user_id == owners[0])) == 1

    asyncio.run(run())


@pytest.mark.parametrize("changes", [
    {"delivery_date": None},
    {"gestation_weeks": None},
    {"gestation_weeks": 19},
    {"gestation_days": None},
    {"gestation_days": 7},
    {"feeding_methods": []},
    {"feeding_methods": ["unsupported"]},
    {"feeding_methods": ["direct", "direct"]},
    {"feeding_methods": ["unknown", "formula"]},
    {"delivery_count": 1, "has_cesarean_history": True},
    {"infant_count": 2},
    {"delivery_date": (date.today() + timedelta(days=1)).isoformat()},
])
def test_invalid_birth_details_are_rejected(changes):
    with pytest.raises(ValidationError):
        OnboardingProfileInput.model_validate(payload(**changes))


@postgres
def test_first_cesarean_is_not_prior_history_and_retry_does_not_duplicate_babies():
    async def run():
        async with database() as (sessions, _booking, _clock, _provider, owners, _episodes):
            data = OnboardingProfileInput.model_validate(payload())
            async with sessions.begin() as session:
                result = await OnboardingService(session).confirm(owners[0], data, "first")
                assert result.status == "completed" and result.primary_infant_id is not None
            async with sessions.begin() as session:
                service = OnboardingService(session)
                again = await service.confirm(owners[0], data, "retry")
                assert again.primary_infant_id == result.primary_infant_id
                assert (await service.read(owners[1])).profile_confirmed is False
                assert (await session.scalar(select(func.count(BabyProfile.id)).where(BabyProfile.owner_user_id == owners[0]))) == 1
                mother = await session.scalar(select(MaternalProfile).where(MaternalProfile.owner_user_id == owners[0]))
                assert mother is not None and mother.latest_delivery_method == "cesarean"
                assert mother.delivery_count == 1 and mother.has_cesarean_history is False
                lactation = await session.scalar(select(LactationProfile).where(LactationProfile.owner_user_id == owners[0]))
                assert lactation is not None and lactation.current_feeding_mode == "mixed_feeding"
                preferences = await session.get(MePreferences, owners[0])
                assert preferences is not None and preferences.profile["gestation_weeks"] == 39
                assert preferences.profile["gestation_days"] == 2
                assert preferences.profile["baby_count"] == 1
                assert preferences.profile["feeding_methods"] == ["direct", "formula"]
                baby = await session.scalar(select(BabyProfile).where(BabyProfile.owner_user_id == owners[0]))
                assert baby is not None and baby.feeding_mode == "mixed_feeding"
                assert await session.scalar(select(UserProfile.preferred_name).where(UserProfile.user_id == owners[0])) == "Mia"
                assert await session.scalar(select(func.count(MaternalCurrentDeliveryInfant.infant_id)).where(MaternalCurrentDeliveryInfant.maternal_profile_id == mother.id)) == 1
                assert await session.get(OnboardingConfirmation, owners[0]) is not None
                with pytest.raises(ApiError) as error:
                    await service.confirm(owners[0], OnboardingProfileInput.model_validate(payload(display_name="Other")), "changed")
                assert error.value.status == 409
    asyncio.run(run())


@postgres
def test_repeat_delivery_tracks_prior_history_separately_from_current_birth():
    async def run():
        async with database() as (sessions, _booking, _clock, _provider, owners, _episodes):
            data = OnboardingProfileInput.model_validate(payload(
                delivery_count=2, has_cesarean_history=False, infant_count=2,
                infants=[{"nickname": "A", "sex": "female"}, {"nickname": "B", "sex": "male"}],
            ))
            async with sessions.begin() as session:
                result = await OnboardingService(session).confirm(owners[0], data, "repeat")
                assert result.profile_confirmed is True
            async with sessions.begin() as session:
                mother = await session.scalar(select(MaternalProfile).where(MaternalProfile.owner_user_id == owners[0]))
                assert mother is not None and mother.delivery_count == 2
                assert mother.latest_delivery_method == "cesarean" and mother.has_cesarean_history is False
                babies = list((await session.scalars(select(BabyProfile).where(BabyProfile.owner_user_id == owners[0]).order_by(BabyProfile.name))).all())
                assert [baby.name for baby in babies] == ["A", "B"]
                assert [baby.birth_date for baby in babies] == [data.delivery_date, data.delivery_date]
    asyncio.run(run())


@postgres
def test_six_infants_are_saved_and_visible_in_me_profile():
    async def run():
        async with database() as (sessions, _booking, _clock, _provider, owners, _episodes):
            data = OnboardingProfileInput.model_validate(payload(
                infant_count=6,
                infants=[{"nickname": f"Baby {index}"} for index in range(1, 7)],
            ))
            async with sessions.begin() as session:
                await OnboardingService(session).confirm(owners[0], data, "six-babies")
            async with sessions.begin() as session:
                preferences = await session.get(MePreferences, owners[0])
                assert preferences is not None and preferences.profile["baby_count"] == 6
                babies = await session.scalar(select(func.count(BabyProfile.id)).where(BabyProfile.owner_user_id == owners[0]))
                assert babies == 6

    asyncio.run(run())


@postgres
def test_existing_current_delivery_babies_are_not_duplicated_by_onboarding():
    async def run():
        async with database() as (sessions, _booking, _clock, _provider, owners, _episodes):
            async with sessions.begin() as session:
                mother = MaternalProfile(owner_user_id=owners[0], delivery_count=2)
                baby = BabyProfile(owner_user_id=owners[0], name="Existing", sex="unspecified")
                session.add_all([mother, baby])
                await session.flush()
                session.add(MaternalCurrentDeliveryInfant(
                    maternal_profile_id=mother.id, infant_id=baby.id, birth_order=1,
                ))
            async with sessions.begin() as session:
                with pytest.raises(ApiError) as error:
                    await OnboardingService(session).confirm(
                        owners[0], OnboardingProfileInput.model_validate(payload()), "existing",
                    )
                assert error.value.status == 409
            async with sessions.begin() as session:
                assert await session.scalar(select(func.count(BabyProfile.id)).where(BabyProfile.owner_user_id == owners[0])) == 1
                assert await session.get(OnboardingConfirmation, owners[0]) is None

    asyncio.run(run())
