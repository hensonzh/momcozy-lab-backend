import base64
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app.api.dependencies import require_current_user
from app.core.settings import Settings
from app.factory import create_app
from app.modules.auth import CurrentUser, issue_access_token
from app.modules.auth.models import DeviceSession
from tests.auth_key_material import TEST_RSA_PRIVATE_KEY_B64, auth_settings


def test_production_settings_require_jwt_private_key() -> None:
    settings = Settings(
        app_env="production",
        object_storage_provider="oss",
        object_storage_bucket="bucket",
        object_storage_access_key_id="access",
        object_storage_secret_access_key="secret",
    )

    with pytest.raises(ValueError, match="AUTH_JWT_PRIVATE_KEY_B64"):
        settings.validate_for_startup()


def test_require_current_user_accepts_valid_bearer_token() -> None:
    user_id = uuid4()
    settings = _auth_settings()
    app = _app_with_me_endpoint(settings)
    token = _encode_token(settings, user_id=user_id, permissions=["files:read"])

    response = TestClient(app).get("/test/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {
        "user_id": str(user_id),
        "permissions": ["files:read"],
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


def test_issue_access_token_uses_rs256_key_id_and_required_claims() -> None:
    user_id = uuid4()
    session_id = uuid4()
    settings = _auth_settings()

    token, expires_in = issue_access_token(user_id=user_id, session_id=session_id, settings=settings)

    header = jwt.get_unverified_header(token)
    payload = jwt.decode(token, options={"verify_signature": False})
    assert header["alg"] == "RS256"
    assert header["typ"] == "JWT"
    assert header["kid"]
    assert expires_in == 900
    assert payload["iss"] == settings.auth_jwt_issuer
    assert payload["aud"] == [
        settings.auth_jwt_product_audience,
        settings.auth_jwt_runtime_audience,
    ]
    assert payload["sub"] == str(user_id)
    assert payload["sid"] == str(session_id)
    assert payload["jti"].startswith("at_")
    assert payload["token_version"] == 1
    assert {"iat", "exp"} <= payload.keys()


@pytest.mark.parametrize(
    "claim",
    ["iss", "aud", "sub", "sid", "jti", "iat", "exp", "token_version", "roles", "permissions"],
)
def test_require_current_user_rejects_missing_required_claim(claim: str) -> None:
    settings = _auth_settings()
    token = _mutated_issued_token(settings, lambda payload: payload.pop(claim))

    response = TestClient(_app_with_me_endpoint(settings)).get(
        "/test/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("roles", "user"),
        ("permissions", "files:read"),
        ("roles", ["user", 1]),
        ("permissions", ["files:read", None]),
        ("roles", ["role"] * 65),
        ("permissions", ["p" * 129]),
    ],
)
def test_require_current_user_rejects_non_string_array_authorization_claims(claim: str, value: object) -> None:
    settings = _auth_settings()
    token = _mutated_issued_token(
        settings,
        lambda payload: payload.update({claim: value}),
    )

    response = TestClient(_app_with_me_endpoint(settings)).get(
        "/test/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_require_current_user_rejects_runtime_only_audience() -> None:
    settings = _auth_settings()
    token = _mutated_issued_token(
        settings,
        lambda payload: payload.update(aud=[settings.auth_jwt_runtime_audience]),
    )

    response = TestClient(_app_with_me_endpoint(settings)).get(
        "/test/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_require_current_user_rejects_non_uuid_session() -> None:
    settings = _auth_settings()
    token = _mutated_issued_token(
        settings,
        lambda payload: payload.update(sid="provider-session"),
    )

    response = TestClient(_app_with_me_endpoint(settings)).get(
        "/test/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Access token session is invalid."


def test_require_current_user_rejects_non_string_token_id() -> None:
    settings = _auth_settings()
    token = _mutated_issued_token(
        settings,
        lambda payload: payload.update(jti=123),
    )

    response = TestClient(_app_with_me_endpoint(settings)).get(
        "/test/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_require_current_user_ignores_retired_scope_claim() -> None:
    settings = _auth_settings()
    token = _mutated_issued_token(
        settings,
        lambda payload: payload.update(permissions=[], scope="plans:read"),
    )

    response = TestClient(_app_with_me_endpoint(settings)).get(
        "/test/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["permissions"] == []


def test_require_current_user_rejects_hs256_token() -> None:
    settings = _auth_settings()
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "iss": settings.auth_jwt_issuer,
            "aud": [
                settings.auth_jwt_product_audience,
                settings.auth_jwt_runtime_audience,
            ],
            "sub": str(uuid4()),
            "sid": str(uuid4()),
            "jti": "at_legacy",
            "iat": now,
            "exp": now + timedelta(minutes=10),
            "token_version": 1,
        },
        "legacy-test-secret-with-at-least-32-bytes",
        algorithm="HS256",
        headers={"kid": "legacy"},
    )

    response = TestClient(_app_with_me_endpoint(settings)).get(
        "/test/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_require_current_user_rejects_unknown_rs256_key_id() -> None:
    settings = _auth_settings()
    token, _expires_in = issue_access_token(
        user_id=uuid4(),
        session_id=uuid4(),
        settings=settings,
    )
    payload = jwt.decode(token, options={"verify_signature": False})
    token = jwt.encode(
        payload,
        base64.b64decode(TEST_RSA_PRIVATE_KEY_B64, validate=True),
        algorithm="RS256",
        headers={"kid": "unknown-key", "typ": "JWT"},
    )

    response = TestClient(_app_with_me_endpoint(settings)).get(
        "/test/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401


def test_require_current_user_accepts_active_device_session_when_enabled() -> None:
    user_id = uuid4()
    session_id = uuid4()
    settings = _auth_settings(auth_require_active_session=True)
    app = _app_with_me_endpoint(settings)
    token = _encode_token(settings, user_id=user_id, session_id=session_id)

    with TestClient(app) as client:
        app.state.db_session_factory = _session_factory(DeviceSession(id=session_id, user_id=user_id, status="active"))
        response = client.get("/test/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["user_id"] == str(user_id)


def test_require_current_user_rejects_revoked_device_session_when_enabled() -> None:
    user_id = uuid4()
    session_id = uuid4()
    settings = _auth_settings(auth_require_active_session=True)
    app = _app_with_me_endpoint(settings)
    token = _encode_token(settings, user_id=user_id, session_id=session_id)

    with TestClient(app) as client:
        app.state.db_session_factory = _session_factory(DeviceSession(id=session_id, user_id=user_id, status="revoked"))
        response = client.get("/test/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Session is no longer active."


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


def _auth_settings(*, auth_require_active_session: bool = False) -> Settings:
    return auth_settings(auth_require_active_session=auth_require_active_session)


def _encode_token(
    settings: Settings,
    *,
    user_id: UUID | str,
    session_id: UUID | str | None = None,
    permissions: list[str] | None = None,
) -> str:
    token, _expires_in = issue_access_token(
        user_id=uuid4(),
        session_id=uuid4(),
        settings=settings,
        roles=frozenset({"user"}),
        permissions=frozenset(permissions or []),
    )

    def mutate(payload: dict) -> None:
        payload["sub"] = str(user_id)
        payload["sid"] = str(session_id or uuid4())

    return _mutated_token(token, mutate)


def _mutated_issued_token(settings: Settings, mutate) -> str:
    token, _expires_in = issue_access_token(
        user_id=uuid4(),
        session_id=uuid4(),
        settings=settings,
        roles=frozenset({"user"}),
    )
    return _mutated_token(token, mutate)


def _mutated_token(token: str, mutate) -> str:
    payload = jwt.decode(token, options={"verify_signature": False})
    mutate(payload)
    headers = jwt.get_unverified_header(token)
    return jwt.encode(
        payload,
        base64.b64decode(TEST_RSA_PRIVATE_KEY_B64, validate=True),
        algorithm="RS256",
        headers={"kid": headers["kid"], "typ": "JWT"},
    )


def _session_factory(device_session: DeviceSession):
    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def get(self, model, session_id):
            if model is DeviceSession and session_id == device_session.id:
                return device_session
            return None

    return FakeSession
