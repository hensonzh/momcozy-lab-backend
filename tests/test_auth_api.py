from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.errors import ApiError
from app.api.dependencies import require_current_user
from app.core.settings import Settings
from app.factory import create_app
from app.modules.auth.account_service import IssuedTokenPair
from app.modules.auth.current_user import CurrentUser
from app.modules.auth.router import get_auth_account_service
from app.modules.users.models import User


def test_signup_returns_token_pair_and_user_contract() -> None:
    fake_service = FakeAuthAccountService()
    client = TestClient(_app(fake_service=fake_service))

    response = client.post(
        "/v1/auth/signup",
        json={"email": "test@example.com", "password": "secret123", "device_id": "ios"},
    )

    assert response.status_code == 201
    assert response.json()["token_type"] == "bearer"
    assert response.json()["access_token"] == "access-token"
    assert response.json()["refresh_token"] == "refresh-token"
    assert response.json()["user"] == {"id": str(fake_service.user.id)}
    assert fake_service.signup_kwargs["email"] == "test@example.com"
    assert fake_service.signup_kwargs["device_context"].device_id == "ios"


def test_signup_rejects_legacy_display_name() -> None:
    client = TestClient(_app(fake_service=FakeAuthAccountService()))

    response = client.post(
        "/v1/auth/signup",
        json={"email": "test@example.com", "password": "secret123", "display_name": "Test"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


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


def test_invite_login_returns_token_pair_and_device_contract() -> None:
    fake_service = FakeAuthAccountService()
    client = TestClient(_app(fake_service=fake_service))

    response = client.post(
        "/v1/auth/invite-login",
        json={"invite_code": "MOMCOZY-BETA", "device_id": "flutter-device-001"},
    )

    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert response.json()["access_token"] == "access-token"
    assert response.json()["refresh_token"] == "refresh-token"
    assert fake_service.invite_login_kwargs["invite_code"] == "MOMCOZY-BETA"
    assert fake_service.invite_login_kwargs["device_context"].device_id == "flutter-device-001"


def test_login_invalid_credentials_use_error_envelope() -> None:
    fake_service = FakeAuthAccountService(
        login_error=ApiError(code="authentication_required", message="Email or password is invalid.", status=401)
    )
    client = TestClient(_app(fake_service=fake_service))

    response = client.post("/v1/auth/login", json={"email": "test@example.com", "password": "wrong"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_signup_validation_error_uses_error_envelope() -> None:
    client = TestClient(_app(fake_service=FakeAuthAccountService()))

    response = client.post("/v1/auth/signup", json={"email": "test@example.com", "password": "short"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_logout_requires_bearer_access_token() -> None:
    client = TestClient(_app(fake_service=FakeAuthAccountService()))

    response = client.post("/v1/auth/logout")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_auth_openapi_keeps_refresh_token_in_body_not_query() -> None:
    schema = _app(fake_service=FakeAuthAccountService()).openapi()
    refresh_operation = schema["paths"]["/v1/auth/refresh"]["post"]
    query_names = {parameter["name"] for parameter in refresh_operation.get("parameters", []) if parameter.get("in") == "query"}

    assert "refresh_token" not in query_names
    assert "requestBody" in refresh_operation


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
        Settings(app_env="test")
    )
    app.dependency_overrides[get_auth_account_service] = lambda: fake_service
    return app


class FakeAuthAccountService:
    def __init__(self, *, login_error: ApiError | None = None) -> None:
        self.user = User(id=uuid4(), status="active")
        self.login_error = login_error
        self.signup_kwargs = None
        self.invite_login_kwargs = None
        self.login_kwargs = None
        self.refresh_kwargs = None
        self.logout_session_id = None

    async def signup(self, **kwargs):
        self.signup_kwargs = kwargs
        return self._issued()

    async def login(self, **kwargs):
        if self.login_error is not None:
            raise self.login_error
        self.login_kwargs = kwargs
        return self._issued()

    async def invite_login(self, **kwargs):
        self.invite_login_kwargs = kwargs
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
