"""The chat-first app contract must not counterfeit legacy postpartum completion."""

import asyncio
from datetime import date, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.dependencies import require_current_user
from app.core.errors import ApiError
from app.core.settings import Settings
from app.factory import create_app
from app.modules.auth import CurrentUser
from app.modules.baby.profile_models import BabyProfile
from app.modules.onboarding.models import ChatFirstOnboarding
from app.modules.onboarding.router import get_onboarding_service
from app.modules.onboarding.schemas import ChatFirstOnboardingInput, ChatFirstStateOutput, OnboardingProfileInput
from app.modules.onboarding.service import OnboardingService
from app.modules.profiles.me_models import MePreferences
from app.modules.profiles.models import LactationProfile, MaternalProfile, UserProfile
from product_database import database, postgres


def mother_payload(**overrides):
    body = {
        "role": "mother", "stage": "postpartum", "age": 32,
        "delivery_count": 2, "delivery_date": (date.today() - timedelta(days=3)).isoformat(),
        "gestation_weeks": 36, "gestation_days": 2, "baby_count": 2,
        "concerns": ["milk-supply", "baby-sleep"],
    }
    body.update(overrides)
    return body


def test_mother_collects_only_demo_fields_and_validates_exact_ranges():
    parsed = ChatFirstOnboardingInput.model_validate(mother_payload())
    assert parsed.role == "mother" and parsed.gestation_days == 2
    assert parsed.concerns == ["milk-supply", "baby-sleep"]
    for changes in (
        {"age": 17}, {"age": 46}, {"delivery_count": 0}, {"delivery_count": 7},
        {"gestation_weeks": 23}, {"gestation_weeks": 43}, {"baby_count": 0},
        {"baby_count": 5}, {"gestation_weeks": 37, "gestation_days": 1},
        {"gestation_days": 7}, {"stage": "pregnancy"},
        {"concerns": ["baby-sleep", "baby-sleep"]}, {"concerns": ["unsupported"]},
        {"delivery_date": (date.today() + timedelta(days=1)).isoformat()},
        {"display_name": "invented"}, {"feeding_methods": ["direct"]},
        {"infants": [{"nickname": "Baby"}]},
    ):
        with pytest.raises(ValidationError):
            ChatFirstOnboardingInput.model_validate(mother_payload(**changes))
    for field in ("stage", "age", "delivery_count", "delivery_date", "gestation_weeks", "gestation_days", "baby_count"):
        invalid = mother_payload()
        del invalid[field]
        with pytest.raises(ValidationError):
            ChatFirstOnboardingInput.model_validate(invalid)


def test_non_mother_role_only_without_maternal_data():
    for role in ("father", "paternal-grandparent", "maternal-grandparent", "other-family"):
        assert ChatFirstOnboardingInput.model_validate({"role": role}).role == role
        with pytest.raises(ValidationError):
            ChatFirstOnboardingInput.model_validate({"role": role, "age": 32})
        with pytest.raises(ValidationError):
            ChatFirstOnboardingInput.model_validate({"role": role, "concerns": ["crying"]})
    with pytest.raises(ValidationError):
        ChatFirstOnboardingInput.model_validate({"role": "stranger"})


def test_chat_first_endpoint_auth_and_owner_scope():
    app = create_app(Settings(app_env="test"))
    assert TestClient(app).get("/v1/onboarding/me/chat-first").status_code == 401
    assert TestClient(app).put("/v1/onboarding/me/chat-first", json={"role": "father"}).status_code == 401
    owner = uuid4()

    class FakeService:
        async def read_chat_first(self, user_id):
            assert user_id == owner
            return ChatFirstStateOutput(status="required", can_enter_app=False, profile_scope="none")

        async def complete_chat_first(self, user_id, payload, request_id):
            assert user_id == owner and payload.role == "father" and request_id == "request-1"
            return ChatFirstStateOutput(
                status="completed", can_enter_app=True, profile_scope="chat_first", role="father",
            )

    async def fake_user():
        return CurrentUser(
            user_id=owner, subject=str(owner), session_id="session", token_id="token",
            roles=frozenset({"user"}), permissions=frozenset(),
        )

    app.dependency_overrides[require_current_user] = fake_user
    app.dependency_overrides[get_onboarding_service] = FakeService
    client = TestClient(app)
    state = client.get("/v1/onboarding/me/chat-first")
    assert state.status_code == 200 and state.json() == {
        "status": "required", "can_enter_app": False, "role": None,
        "profile_scope": "none", "primary_infant_id": None,
    }
    assert state.headers["cache-control"] == "private, no-store"
    saved = client.put(
        "/v1/onboarding/me/chat-first", json={"role": "father"}, headers={"X-Request-ID": "request-1"},
    )
    assert saved.status_code == 200 and saved.json()["role"] == "father"
    assert saved.headers["cache-control"] == "private, no-store"


@postgres
def test_chat_first_mother_save_is_atomic_owner_scoped_and_replay_safe():
    async def run():
        async with database() as (sessions, _, _, _, owners, _):
            body = mother_payload(client_timezone_offset_minutes=480)
            async with sessions.begin() as session:
                service = OnboardingService(session)
                assert (await service.read_chat_first(owners[0])).can_enter_app is False
                first = await service.complete_chat_first(
                    owners[0], ChatFirstOnboardingInput.model_validate(body), "first",
                )
                assert first.role == "mother" and first.profile_scope == "chat_first"
                assert first.can_enter_app and first.primary_infant_id is None
                # Legacy clients still see the old full-profile gate, not a fictitious infant.
                assert (await service.read(owners[0])).profile_confirmed is False
                assert (await service.read_chat_first(owners[1])).can_enter_app is False
            async with sessions.begin() as session:
                service = OnboardingService(session)
                assert await service.complete_chat_first(
                    owners[0], ChatFirstOnboardingInput.model_validate(
                        mother_payload(client_timezone_offset_minutes=-300)
                    ), "replay",
                ) == first
                with pytest.raises(ApiError) as conflict:
                    await service.complete_chat_first(
                        owners[0], ChatFirstOnboardingInput.model_validate(mother_payload(age=33)), "changed",
                    )
                assert conflict.value.status == 409
            async with sessions.begin() as session:
                user = await session.scalar(select(UserProfile).where(UserProfile.user_id == owners[0]))
                maternal = await session.scalar(select(MaternalProfile).where(MaternalProfile.owner_user_id == owners[0]))
                preferences = await session.get(MePreferences, owners[0])
                assert user is not None and user.age == 32 and user.preferred_name is None
                assert maternal is not None and maternal.delivery_count == 2
                assert maternal.latest_delivery_date.isoformat() == body["delivery_date"]
                assert preferences is not None and preferences.profile["gestation_weeks"] == 36
                assert preferences.profile["gestation_days"] == 2 and preferences.profile["baby_count"] == 2
                assert "feeding_methods" not in preferences.profile
                assert [item["note"] for item in preferences.concerns] == ["奶量够不够", "宝宝睡得好吗"]
                assert await session.get(ChatFirstOnboarding, owners[0]) is not None
                assert await session.scalar(select(func.count(BabyProfile.id)).where(BabyProfile.owner_user_id == owners[0])) == 0
                assert await session.scalar(select(func.count(LactationProfile.id)).where(LactationProfile.owner_user_id == owners[0])) == 0
    asyncio.run(run())


@postgres
def test_non_mother_and_legacy_profiles_do_not_gain_fictitious_maternal_facts():
    async def run():
        async with database() as (sessions, _, _, _, owners, _):
            async with sessions.begin() as session:
                service = OnboardingService(session)
                father = await service.complete_chat_first(
                    owners[0], ChatFirstOnboardingInput.model_validate({"role": "father"}), "father",
                )
                assert father.can_enter_app and father.role == "father"
                assert (await service.read(owners[0])).status == "required"
            async with sessions.begin() as session:
                assert await session.scalar(select(func.count(MaternalProfile.id)).where(MaternalProfile.owner_user_id == owners[0])) == 0
                assert await session.scalar(select(func.count(UserProfile.id)).where(UserProfile.user_id == owners[0])) == 0
                await OnboardingService(session).confirm(
                    owners[1], OnboardingProfileInput.model_validate({
                        "stage": "postpartum", "display_name": "Mia", "age": 32,
                        "delivery_date": (date.today() - timedelta(days=3)).isoformat(),
                        "delivery_count": 1, "gestation_weeks": 39, "gestation_days": 0,
                        "feeding_methods": ["direct"], "infant_count": 1, "infants": [{}],
                    }), "legacy",
                )
                upgraded = await OnboardingService(session).read_chat_first(owners[1])
                assert upgraded.can_enter_app and upgraded.role == "mother"
                assert upgraded.profile_scope == "legacy_full" and upgraded.primary_infant_id is not None
    asyncio.run(run())
