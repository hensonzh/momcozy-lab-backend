from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from production_backend.app.api.dependencies import require_current_user
from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app
from production_backend.app.modules.auth import CurrentUser, issue_access_token


def test_production_settings_require_jwt_secret() -> None:
    settings = Settings(
        app_env="production",
        object_storage_provider="oss",
        object_storage_bucket="bucket",
        object_storage_access_key_id="access",
        object_storage_secret_access_key="secret",
    )

    with pytest.raises(ValueError, match="AUTH_JWT_SECRET"):
        settings.validate_for_startup()


def test_configured_jwt_secret_must_be_at_least_32_bytes() -> None:
    settings = Settings(app_env="test", auth_jwt_secret="short")

    with pytest.raises(ValueError, match="at least 32 bytes"):
        settings.validate_for_startup()


def test_require_current_user_accepts_valid_bearer_token() -> None:
    user_id = uuid4()
    settings = _auth_settings()
    app = _app_with_me_endpoint(settings)
    token = _encode_token(settings, user_id=user_id, permissions=["files:read"], scope="plans:read")

    response = TestClient(app).get("/test/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {
        "user_id": str(user_id),
        "permissions": ["files:read", "plans:read"],
        "roles": ["user"],
    }


def test_require_current_user_rejects_missing_token() -> None:
    response = TestClient(_app_with_me_endpoint(_auth_settings())).get("/test/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_require_current_user_rejects_invalid_token() -> None:
    response = TestClient(_app_with_me_endpoint(_auth_settings())).get(
        "/test/me",
        headers={"Authorization": "Bearer not-a-token"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_require_current_user_rejects_non_uuid_subject() -> None:
    settings = _auth_settings()
    token = _encode_token(settings, user_id="provider-subject")

    response = TestClient(_app_with_me_endpoint(settings)).get("/test/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Access token subject is invalid."


def test_issue_access_token_can_be_authenticated() -> None:
    user_id = uuid4()
    session_id = uuid4()
    settings = _auth_settings()
    token, expires_in = issue_access_token(user_id=user_id, session_id=session_id, settings=settings)

    response = TestClient(_app_with_me_endpoint(settings)).get("/test/me", headers={"Authorization": f"Bearer {token}"})

    assert expires_in == 900
    assert response.status_code == 200
    assert response.json()["user_id"] == str(user_id)


def _app_with_me_endpoint(settings: Settings):
    app = create_app(settings)

    @app.get("/test/me")
    async def me(current_user: CurrentUser = Depends(require_current_user)) -> dict[str, object]:
        return {
            "user_id": str(current_user.user_id),
            "permissions": sorted(current_user.permissions),
            "roles": sorted(current_user.roles),
        }

    return app


def _auth_settings() -> Settings:
    return Settings(
        app_env="test",
        auth_jwt_secret="test-secret-value-with-at-least-32-bytes",
        auth_jwt_issuer="momcozy-test",
        auth_jwt_audience="momcozy-app",
    )


def _encode_token(settings: Settings, *, user_id: UUID | str, permissions: list[str] | None = None, scope: str = "") -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iss": settings.auth_jwt_issuer,
        "aud": settings.auth_jwt_audience,
        "exp": now + timedelta(minutes=10),
        "iat": now,
        "sid": "session-1",
        "jti": "token-1",
        "roles": ["user"],
        "permissions": permissions or [],
        "scope": scope,
    }
    return jwt.encode(payload, settings.auth_jwt_secret, algorithm=settings.auth_jwt_algorithm)
