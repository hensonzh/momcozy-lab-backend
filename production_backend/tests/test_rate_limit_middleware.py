from fastapi.testclient import TestClient

from production_backend.app.core.settings import Settings
from production_backend.app.factory import create_app


def test_rate_limit_returns_stable_error_envelope() -> None:
    app = create_app(
        Settings(
            app_env="test",
            rate_limit_enabled=True,
            rate_limit_requests=1,
            rate_limit_window_seconds=30,
        )
    )

    with TestClient(app) as client:
        app.state.redis_client = FakeRedis()
        first = client.post("/v1/auth/login", json={})
        second = client.post("/v1/auth/login", json={})

    assert first.status_code == 422
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"
    assert second.json()["error"]["details"] == {"retry_after_seconds": 30}
    assert second.headers["Retry-After"] == "30"
    assert second.headers["X-RateLimit-Limit"] == "1"
    assert second.headers["X-RateLimit-Remaining"] == "0"
    assert second.headers["X-Request-ID"]


def test_rate_limit_exempts_health_endpoints() -> None:
    app = create_app(
        Settings(
            app_env="test",
            rate_limit_enabled=True,
            rate_limit_requests=1,
            rate_limit_window_seconds=30,
        )
    )

    with TestClient(app) as client:
        app.state.redis_client = FakeRedis()
        first = client.get("/v1/health/live")
        second = client.get("/v1/health/live")

    assert first.status_code == 200
    assert second.status_code == 200
    assert "X-RateLimit-Limit" not in second.headers


def test_rate_limit_uses_hashed_credentials_as_identity() -> None:
    app = create_app(
        Settings(
            app_env="test",
            rate_limit_enabled=True,
            rate_limit_requests=1,
            rate_limit_window_seconds=30,
        )
    )

    with TestClient(app) as client:
        fake_redis = FakeRedis()
        app.state.redis_client = fake_redis
        client.post("/v1/auth/login", json={}, headers={"Authorization": "Bearer token-a"})
        client.post("/v1/auth/login", json={}, headers={"Authorization": "Bearer token-b"})

    assert len(fake_redis.values) == 2
    assert all("token-a" not in key and "token-b" not in key for key in fake_redis.values)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    async def incr(self, name: str) -> int:
        self.values[name] = self.values.get(name, 0) + 1
        return self.values[name]

    async def expire(self, name: str, time: int) -> bool:
        return name in self.values and time > 0
