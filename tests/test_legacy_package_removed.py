from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.factory import create_app


def test_legacy_package_routes_are_not_exposed() -> None:
    app = create_app(Settings(app_env="test"))
    paths = app.openapi()["paths"]
    assert not any(path.startswith("/v1/care/") for path in paths)
    assert not any(path.startswith("/v1/ibclc/") for path in paths)
    assert not any(path.startswith("/v1/notifications/appointments/") for path in paths)
    assert "/v1/plans" in paths
    with TestClient(app) as client:
        assert client.get("/v1/care/catalog").status_code == 404
