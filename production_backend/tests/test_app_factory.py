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
