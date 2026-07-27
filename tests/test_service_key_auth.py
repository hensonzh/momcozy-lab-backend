from fastapi import Depends
from fastapi.testclient import TestClient

from app.api.dependencies import require_agent_runtime_client, require_service_client
from app.core.settings import Settings
from app.factory import create_app
from app.modules.auth import ServiceClient


SERVICE_KEY = "service-key-value-with-at-least-32-bytes"
AGENT_RUNTIME_SERVICE_KEY = "agent-runtime-service-key-with-at-least-32-bytes"


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


def test_require_agent_runtime_client_accepts_dedicated_key() -> None:
    response = TestClient(_app()).get(
        "/test/agent-runtime-service",
        headers={"X-Service-Key": AGENT_RUNTIME_SERVICE_KEY},
    )

    assert response.status_code == 200
    assert response.json() == {"service": "agent-runtime"}


def test_require_agent_runtime_client_rejects_missing_key() -> None:
    response = TestClient(_app()).get("/test/agent-runtime-service")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_service_credentials_are_not_interchangeable() -> None:
    client = TestClient(_app())

    generic_response = client.get(
        "/test/service",
        headers={"X-Service-Key": AGENT_RUNTIME_SERVICE_KEY},
    )
    agent_runtime_response = client.get(
        "/test/agent-runtime-service",
        headers={"X-Service-Key": SERVICE_KEY},
    )

    assert generic_response.status_code == 401
    assert agent_runtime_response.status_code == 401


def test_require_agent_runtime_client_fails_when_not_configured() -> None:
    response = TestClient(_app(agent_runtime_service_api_key="")).get(
        "/test/agent-runtime-service",
        headers={"X-Service-Key": AGENT_RUNTIME_SERVICE_KEY},
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "service_auth_not_configured"


def test_configured_service_key_must_be_at_least_32_bytes() -> None:
    settings = Settings(app_env="test", service_api_key="short")

    try:
        settings.validate_for_startup()
    except ValueError as exc:
        assert "SERVICE_API_KEY" in str(exc)
    else:
        raise AssertionError("expected SERVICE_API_KEY validation error")


def test_configured_agent_runtime_service_key_must_be_at_least_32_bytes() -> None:
    settings = Settings(app_env="test", agent_runtime_service_api_key="short")

    try:
        settings.validate_for_startup()
    except ValueError as exc:
        assert "AGENT_RUNTIME_SERVICE_API_KEY" in str(exc)
    else:
        raise AssertionError("expected AGENT_RUNTIME_SERVICE_API_KEY validation error")


def test_agent_runtime_service_key_must_differ_from_operational_service_key() -> None:
    settings = Settings(
        app_env="test",
        service_api_key=SERVICE_KEY,
        agent_runtime_service_api_key=SERVICE_KEY,
    )

    try:
        settings.validate_for_startup()
    except ValueError as exc:
        assert "AGENT_RUNTIME_SERVICE_API_KEY must differ from SERVICE_API_KEY" in str(exc)
    else:
        raise AssertionError("expected service identity isolation validation error")


def _app(*, agent_runtime_service_api_key: str = AGENT_RUNTIME_SERVICE_KEY):
    app = create_app(
        Settings(
            app_env="test",
            service_api_key=SERVICE_KEY,
            agent_runtime_service_api_key=agent_runtime_service_api_key,
        )
    )

    @app.get("/test/service")
    async def service(client: ServiceClient = Depends(require_service_client)) -> dict[str, str]:
        return {"service": client.name}

    @app.get("/test/agent-runtime-service")
    async def agent_runtime_service(
        client: ServiceClient = Depends(require_agent_runtime_client),
    ) -> dict[str, str]:
        return {"service": client.name}

    return app
