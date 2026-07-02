from fastapi import Depends
from fastapi.testclient import TestClient

from production_backend.app.api.dependencies import require_service_client
from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app
from production_backend.app.modules.auth import ServiceClient


SERVICE_KEY = "service-key-value-with-at-least-32-bytes"


def test_require_service_client_accepts_configured_key() -> None:
    response = TestClient(_app()).get("/test/service", headers={"X-Service-Key": SERVICE_KEY})

    assert response.status_code == 200
    assert response.json() == {"service": "internal-service"}


def test_require_service_client_rejects_missing_key() -> None:
    response = TestClient(_app()).get("/test/service")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_require_service_client_rejects_invalid_key() -> None:
    response = TestClient(_app()).get("/test/service", headers={"X-Service-Key": "wrong"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_configured_service_key_must_be_at_least_32_bytes() -> None:
    settings = Settings(app_env="test", service_api_key="short")

    try:
        settings.validate_for_startup()
    except ValueError as exc:
        assert "SERVICE_API_KEY" in str(exc)
    else:
        raise AssertionError("expected SERVICE_API_KEY validation error")


def _app():
    app = create_app(Settings(app_env="test", service_api_key=SERVICE_KEY))

    @app.get("/test/service")
    async def service(client: ServiceClient = Depends(require_service_client)) -> dict[str, str]:
        return {"service": client.name}

    return app
