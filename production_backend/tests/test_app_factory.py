from fastapi.testclient import TestClient

from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app


def test_create_app_uses_injected_settings() -> None:
    app = create_app(Settings(app_name="Test Backend", app_version="9.9.9", app_env="test"))

    assert app.title == "Test Backend"
    assert app.version == "9.9.9"


def test_health_endpoints_are_registered() -> None:
    client = TestClient(create_app(Settings(app_env="test")))

    assert client.get("/v1/health/live").json() == {"status": "ok"}
    assert client.get("/v1/health/ready").json() == {"status": "ok"}


def test_cors_is_disabled_when_no_allowed_origins_are_configured() -> None:
    client = TestClient(create_app(Settings(app_env="test")))

    response = client.get("/v1/health/live", headers={"Origin": "https://app.example.test"})

    assert "access-control-allow-origin" not in response.headers


def test_cors_preflight_uses_configured_origins_and_headers() -> None:
    client = TestClient(create_app(Settings(app_env="test", cors_allowed_origins=("https://app.example.test",))))

    response = client.options(
        "/v1/health/live",
        headers={
            "Origin": "https://app.example.test",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization,Idempotency-Key,X-Request-ID",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://app.example.test"
    assert "authorization" in response.headers["access-control-allow-headers"].lower()

    actual_response = client.get("/v1/health/live", headers={"Origin": "https://app.example.test"})
    assert "x-request-id" in actual_response.headers["access-control-expose-headers"].lower()
