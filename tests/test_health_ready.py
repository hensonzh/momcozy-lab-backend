from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.factory import create_app


def test_ready_skips_infrastructure_checks_for_local_test_settings() -> None:
    response = TestClient(create_app(Settings(app_env="test"))).get("/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_checks_infrastructure_when_enabled() -> None:
    app = create_app(Settings(app_env="test", readiness_check_infrastructure=True))

    with TestClient(app) as client:
        app.state.db_engine = FakeDbEngine()
        app.state.redis_client = FakeRedis()
        response = client.get("/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


def test_ready_returns_dependency_failed_when_redis_check_fails() -> None:
    app = create_app(Settings(app_env="test", readiness_check_infrastructure=True))

    with TestClient(app) as client:
        app.state.db_engine = FakeDbEngine()
        app.state.redis_client = FakeRedis(should_fail=True)
        response = client.get("/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_failed"
    assert response.json()["error"]["message"] == "Redis readiness check failed."


class FakeDbEngine:
    def connect(self):
        return FakeDbConnection()


class FakeDbConnection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def execute(self, statement):
        return None


class FakeRedis:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail

    async def ping(self) -> bool:
        if self.should_fail:
            raise RuntimeError("redis unavailable")
        return True
