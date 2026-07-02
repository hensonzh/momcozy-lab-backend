import logging

from fastapi import Query
from fastapi.testclient import TestClient

from production_backend.app.core.errors import ApiError
from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app


def test_request_id_header_is_preserved() -> None:
    client = TestClient(create_app(Settings(app_env="test")))

    response = client.get("/v1/health/live", headers={"X-Request-ID": "req_test"})

    assert response.headers["X-Request-ID"] == "req_test"
    assert response.status_code == 200


def test_not_found_uses_error_envelope() -> None:
    client = TestClient(create_app(Settings(app_env="test")))

    response = client.get("/missing")

    body = response.json()
    assert response.status_code == 404
    assert body["error"]["code"] == "not_found"
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]


def test_api_error_uses_stable_code_and_details() -> None:
    app = create_app(Settings(app_env="test"))

    @app.get("/test/api-error")
    async def raise_api_error() -> None:
        raise ApiError(code="permission_denied", message="No access.", status=403, details={"resource": "thing"})

    client = TestClient(app)
    response = client.get("/test/api-error")

    assert response.status_code == 403
    assert response.json()["error"] == {
        "code": "permission_denied",
        "message": "No access.",
        "request_id": response.headers["X-Request-ID"],
        "details": {"resource": "thing"},
    }


def test_validation_error_uses_error_envelope() -> None:
    app = create_app(Settings(app_env="test"))

    @app.get("/test/validation")
    async def validate_limit(limit: int = Query(ge=1)) -> dict[str, int]:
        return {"limit": limit}

    client = TestClient(app)
    response = client.get("/test/validation?limit=0")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_validation_error_does_not_echo_sensitive_input_values() -> None:
    with TestClient(create_app(Settings(app_env="test"))) as client:
        response = client.post("/v1/auth/signup", json={"email": "mai@example.test", "password": "sekrit"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert "sekrit" not in response.text
    assert all("input" not in item for item in error["details"]["errors"])


def test_unhandled_error_hides_exception_text(caplog) -> None:
    app = create_app(Settings(app_env="test"))

    @app.get("/test/unhandled")
    async def raise_unhandled() -> None:
        raise RuntimeError("database password leaked")

    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR, logger="production_backend.errors"):
        response = client.get("/test/unhandled", headers={"X-Request-ID": "req_unhandled"})

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert response.json()["error"]["message"] == "Internal server error."
    assert "database password leaked" not in response.text
    assert "http.unhandled_exception" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "req_unhandled" in caplog.text
    assert "database password leaked" not in caplog.text
