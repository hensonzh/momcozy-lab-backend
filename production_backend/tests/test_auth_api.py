from uuid import uuid4

from fastapi.testclient import TestClient

from production_backend.app.api.dependencies import require_current_user
from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app
from production_backend.app.modules.auth.account_service import IssuedTokenPair
from production_backend.app.modules.auth.current_user import CurrentUser
from production_backend.app.modules.auth.router import get_auth_account_service
from production_backend.app.modules.users.models import User


def test_signup_returns_token_pair_and_user_contract() -> None:
    fake_service = FakeAuthAccountService()
    client = TestClient(_app(fake_service=fake_service))

    response = client.post(
        "/v1/auth/signup",
        json={"email": "test@example.com", "password": "secret123", "display_name": "Test", "device_id": "ios"},
    )

    assert response.status_code == 201
    assert response.json()["token_type"] == "bearer"
    assert response.json()["access_token"] == "access-token"
    assert response.json()["refresh_token"] == "refresh-token"
    assert fake_service.signup_kwargs["email"] == "test@example.com"
    assert fake_service.signup_kwargs["device_context"].device_id == "ios"


def test_login_and_refresh_return_same_token_contract() -> None:
    fake_service = FakeAuthAccountService()
    client = TestClient(_app(fake_service=fake_service))

    login = client.post("/v1/auth/login", json={"email": "test@example.com", "password": "secret123"})
    refresh = client.post("/v1/auth/refresh", json={"refresh_token": "old-refresh"})

    assert login.status_code == 200
    assert refresh.status_code == 200
    assert login.json()["expires_in"] == 900
    assert fake_service.login_kwargs["email"] == "test@example.com"
    assert fake_service.refresh_kwargs["refresh_token"] == "old-refresh"


def test_logout_revokes_current_access_token_session() -> None:
    session_id = uuid4()
    fake_service = FakeAuthAccountService()
    app = _app(fake_service=fake_service)
    app.dependency_overrides[require_current_user] = lambda: CurrentUser(
        user_id=uuid4(),
        subject="user",
        session_id=str(session_id),
        token_id="token",
        roles=frozenset({"user"}),
        permissions=frozenset(),
    )
    client = TestClient(app)

    response = client.post("/v1/auth/logout", headers={"Authorization": "Bearer token"})

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert fake_service.logout_session_id == session_id


def _app(*, fake_service: "FakeAuthAccountService"):
    app = create_app(
        Settings(
            app_env="test",
            auth_jwt_secret="test-secret-value-with-at-least-32-bytes",
            auth_jwt_issuer="momcozy-test",
            auth_jwt_audience="momcozy-app",
        )
    )
    app.dependency_overrides[get_auth_account_service] = lambda: fake_service
    return app


class FakeAuthAccountService:
    def __init__(self) -> None:
        self.user = User(id=uuid4(), display_name="Test", status="active")
        self.signup_kwargs = None
        self.login_kwargs = None
        self.refresh_kwargs = None
        self.logout_session_id = None

    async def signup(self, **kwargs):
        self.signup_kwargs = kwargs
        return self._issued()

    async def login(self, **kwargs):
        self.login_kwargs = kwargs
        return self._issued()

    async def refresh(self, **kwargs):
        self.refresh_kwargs = kwargs
        return self._issued()

    async def logout(self, *, session_id):
        self.logout_session_id = session_id

    def _issued(self):
        return IssuedTokenPair(
            user=self.user,
            access_token="access-token",
            refresh_token="refresh-token",
            expires_in=900,
        )
