from datetime import date
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.factory import create_app
from app.modules.auth import CurrentUser
from app.modules.profiles.models import InfantProfile, UserProfile
from app.modules.profiles.router import get_profile_service


def test_get_my_profile_requires_current_user() -> None:
    response = TestClient(create_app(Settings(app_env="test"))).get("/v1/profile/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_get_my_profile_uses_current_user_scope() -> None:
    user_id = uuid4()
    fake_service = FakeProfileService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_profile_service] = lambda: fake_service

    response = TestClient(app).get("/v1/profile/me")

    assert response.status_code == 200
    assert response.json() == {
        "preferred_name": "Mia",
        "age": 32,
        "estimated_due_date": "2026-09-20",
    }
    assert fake_service.get_profile_kwargs["user_id"] == user_id


def test_patch_my_profile_normalizes_and_passes_all_user_editable_fields() -> None:
    user_id = uuid4()
    fake_service = FakeProfileService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_profile_service] = lambda: fake_service

    response = TestClient(app).patch(
        "/v1/profile/me",
        headers={"X-Request-ID": "req_profile"},
        json={
            "preferred_name": " Mia ",
            "age": 32,
            "estimated_due_date": "2026-09-20",
        },
    )

    assert response.status_code == 200
    assert fake_service.update_profile_kwargs["user_id"] == user_id
    assert fake_service.update_profile_kwargs["values"] == {
        "preferred_name": "Mia",
        "age": 32,
        "estimated_due_date": date(2026, 9, 20),
    }
    assert fake_service.update_profile_kwargs["request_id"] == "req_profile"


def test_patch_my_profile_rejects_empty_update_and_put_is_not_supported() -> None:
    user_id = uuid4()
    fake_service = FakeProfileService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_profile_service] = lambda: fake_service

    assert TestClient(app).patch("/v1/profile/me", json={}).status_code == 422
    assert TestClient(app).put("/v1/profile/me", json={"age": 32}).status_code == 405
    assert fake_service.update_profile_kwargs == {}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lactation_advice", "retired"),
        ("feeding_advice", "retired"),
        ("profile_onboarding_skipped_at", "2026-07-22T00:00:00Z"),
        ("profile_onboarding_completed_at", "2026-07-22T00:00:00Z"),
        ("display_name", "retired"),
        ("delivery_date", "2026-09-20"),
    ],
)
def test_update_my_profile_rejects_retired_fields(field: str, value: str) -> None:
    user_id = uuid4()
    fake_service = FakeProfileService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_profile_service] = lambda: fake_service

    response = TestClient(app).patch("/v1/profile/me", json={field: value})

    assert response.status_code == 422
    assert fake_service.update_profile_kwargs == {}


def test_create_my_infant_uses_current_user_and_idempotency_key() -> None:
    user_id = uuid4()
    fake_service = FakeProfileService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_profile_service] = lambda: fake_service

    response = TestClient(app).post(
        "/v1/profile/infants",
        headers={"X-Request-ID": "req_infant", "Idempotency-Key": " idem-infant "},
        json={"name": " Baby ", "sex_at_birth": "female", "birth_date": "2026-06-01"},
    )

    assert response.status_code == 201
    assert fake_service.create_infant_kwargs["owner_user_id"] == user_id
    assert fake_service.create_infant_kwargs["request_id"] == "req_infant"
    assert fake_service.create_infant_kwargs["idempotency_key"] == "idem-infant"
    assert fake_service.create_infant_kwargs["name"] == "Baby"
    assert fake_service.create_infant_kwargs["sex_at_birth"] == "female"


@pytest.mark.parametrize(
    "payload",
    [
        {"infant_name": "Baby"},
        {"name": "Baby", "sex": "female"},
        {"name": "Baby", "sex_at_birth": "unsupported"},
        {"name": "Baby", "birth_date": "2999-01-01"},
    ],
)
def test_create_my_infant_rejects_legacy_or_invalid_fields(payload: dict[str, str]) -> None:
    user_id = uuid4()
    fake_service = FakeProfileService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_profile_service] = lambda: fake_service

    response = TestClient(app).post("/v1/profile/infants", json=payload)

    assert response.status_code == 422
    assert fake_service.create_infant_kwargs == {}


def test_list_my_infants_uses_current_user_scope() -> None:
    user_id = uuid4()
    fake_service = FakeProfileService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_profile_service] = lambda: fake_service

    response = TestClient(app).get("/v1/profile/infants")

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "id": str(fake_service.infant_id),
            "name": "Baby",
            "sex_at_birth": "female",
            "birth_date": "2026-06-01",
        }
    ]
    assert fake_service.list_infants_kwargs["owner_user_id"] == user_id


def _override_current_user(app, user_id: UUID) -> None:
    from app.api.dependencies import require_current_user

    async def fake_current_user() -> CurrentUser:
        return CurrentUser(
            user_id=user_id,
            subject=str(user_id),
            session_id="session",
            token_id="token",
            roles=frozenset({"user"}),
            permissions=frozenset(),
        )

    app.dependency_overrides[require_current_user] = fake_current_user


class FakeProfileService:
    def __init__(self, *, user_id: UUID) -> None:
        self.user_id = user_id
        self.infant_id = uuid4()
        self.get_profile_kwargs = {}
        self.update_profile_kwargs = {}
        self.create_infant_kwargs = {}
        self.list_infants_kwargs = {}

    async def get_user_profile(self, **kwargs):
        self.get_profile_kwargs = kwargs
        return self._profile()

    async def update_user_profile(self, **kwargs):
        self.update_profile_kwargs = kwargs
        return self._profile(
            preferred_name=kwargs["values"].get("preferred_name", "Mia"),
            estimated_due_date=kwargs["values"].get(
                "estimated_due_date",
                date(2026, 9, 20),
            ),
        )

    async def list_infants(self, **kwargs):
        self.list_infants_kwargs = kwargs
        return [self._infant()]

    async def create_infant(self, **kwargs):
        self.create_infant_kwargs = kwargs
        return self._infant(
            name=kwargs["name"],
            sex_at_birth=kwargs["sex_at_birth"],
            birth_date=kwargs["birth_date"],
        )

    def _profile(
        self,
        *,
        preferred_name: str | None = "Mia",
        estimated_due_date: date | None = date(2026, 9, 20),
    ) -> UserProfile:
        return UserProfile(
            id=uuid4(),
            user_id=self.user_id,
            preferred_name=preferred_name,
            age=32,
            estimated_due_date=estimated_due_date,
        )

    def _infant(
        self,
        *,
        name: str = "Baby",
        sex_at_birth: str | None = "female",
        birth_date: date | None = date(2026, 6, 1),
    ) -> InfantProfile:
        return InfantProfile(
            id=self.infant_id,
            owner_user_id=self.user_id,
            name=name,
            sex_at_birth=sex_at_birth,
            birth_date=birth_date,
        )
