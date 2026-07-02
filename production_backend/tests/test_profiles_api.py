from datetime import date
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app
from production_backend.app.modules.auth import CurrentUser
from production_backend.app.modules.profiles.models import InfantProfile, UserProfile
from production_backend.app.modules.profiles.router import get_profile_service


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
    assert response.json()["user_id"] == str(user_id)
    assert fake_service.get_profile_kwargs["user_id"] == user_id


def test_update_my_profile_passes_request_id_and_values() -> None:
    user_id = uuid4()
    fake_service = FakeProfileService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_profile_service] = lambda: fake_service

    response = TestClient(app).put(
        "/v1/profile/me",
        headers={"X-Request-ID": "req_profile"},
        json={"display_name": "Mia", "age": 32},
    )

    assert response.status_code == 200
    assert fake_service.update_profile_kwargs["user_id"] == user_id
    assert fake_service.update_profile_kwargs["values"] == {"display_name": "Mia", "age": 32}
    assert fake_service.update_profile_kwargs["request_id"] == "req_profile"


def test_create_my_infant_uses_current_user_and_idempotency_key() -> None:
    user_id = uuid4()
    fake_service = FakeProfileService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_profile_service] = lambda: fake_service

    response = TestClient(app).post(
        "/v1/profile/infants",
        headers={"X-Request-ID": "req_infant", "Idempotency-Key": " idem-infant "},
        json={"infant_name": "Baby", "sex": "female", "birth_date": "2026-06-01"},
    )

    assert response.status_code == 201
    assert fake_service.create_infant_kwargs["owner_user_id"] == user_id
    assert fake_service.create_infant_kwargs["request_id"] == "req_infant"
    assert fake_service.create_infant_kwargs["idempotency_key"] == "idem-infant"


def test_list_my_infants_uses_current_user_scope() -> None:
    user_id = uuid4()
    fake_service = FakeProfileService(user_id=user_id)
    app = create_app(Settings(app_env="test"))
    _override_current_user(app, user_id)
    app.dependency_overrides[get_profile_service] = lambda: fake_service

    response = TestClient(app).get("/v1/profile/infants")

    assert response.status_code == 200
    assert response.json()["items"][0]["owner_user_id"] == str(user_id)
    assert fake_service.list_infants_kwargs["owner_user_id"] == user_id


def _override_current_user(app, user_id: UUID) -> None:
    from production_backend.app.api.dependencies import require_current_user

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
        return self._profile(display_name=kwargs["values"].get("display_name", ""))

    async def list_infants(self, **kwargs):
        self.list_infants_kwargs = kwargs
        return [self._infant()]

    async def create_infant(self, **kwargs):
        self.create_infant_kwargs = kwargs
        return self._infant(
            infant_name=kwargs["infant_name"],
            sex=kwargs["sex"],
            birth_date=kwargs["birth_date"],
        )

    def _profile(self, *, display_name: str = "Mia") -> UserProfile:
        return UserProfile(id=uuid4(), user_id=self.user_id, display_name=display_name, age=32)

    def _infant(
        self,
        *,
        infant_name: str = "Baby",
        sex: str = "female",
        birth_date: date | None = date(2026, 6, 1),
    ) -> InfantProfile:
        return InfantProfile(
            id=self.infant_id,
            owner_user_id=self.user_id,
            infant_name=infant_name,
            sex=sex,
            birth_date=birth_date,
            status="active",
        )
