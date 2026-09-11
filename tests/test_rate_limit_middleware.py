from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.factory import create_app
from tests.auth_key_material import auth_settings


def test_account_limit_uses_authentication_email_normalization_across_ips() -> None:
    import asyncio
    import json
    from starlette.requests import Request
    from app.core.rate_limit import check_rate_limit

    async def run():
        settings = Settings(app_env='test', rate_limit_enabled=False)
        app = create_app(settings)
        app.state.redis_client = FakeRedis()

        async def attempt(email, index):
            async def receive():
                return {'type': 'http.request', 'body': json.dumps({'email': email}).encode()}
            request = Request({'type': 'http', 'method': 'POST', 'path': '/v1/auth/login',
                'headers': [], 'scheme': 'http', 'server': ('test', 80),
                'client': (f'192.0.2.{index}', 1234), 'app': app}, receive)
            return await check_rate_limit(request, settings)

        for index in range(30):
            assert (await attempt('mia@example.com', index)).allowed
        assert not (await attempt('mia@example.com', 30)).allowed
        for index, email in enumerate(['mia@ｅxample.com', ' Mia@EXAMPLE.COM ', 'mia@ｅｘａｍｐｌｅ.com'], 31):
            assert not (await attempt(email, index)).allowed
        assert (await attempt('other@example.com', 40)).allowed
    asyncio.run(run())


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


def test_rate_limit_exempts_jwks_endpoint() -> None:
    app = create_app(
        auth_settings(
            rate_limit_enabled=True,
            rate_limit_requests=1,
            rate_limit_window_seconds=30,
        )
    )

    with TestClient(app) as client:
        app.state.redis_client = FakeRedis()
        first = client.get("/.well-known/jwks.json")
        second = client.get("/.well-known/jwks.json")

    assert first.status_code == 200
    assert second.status_code == 200
    assert "X-RateLimit-Limit" not in second.headers


def test_auth_rate_limit_cannot_be_bypassed_by_arbitrary_bearer_headers() -> None:
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

    assert len(fake_redis.values) == 1
    assert all("token-a" not in key and "token-b" not in key for key in fake_redis.values)


def test_agent_runtime_internal_surface_uses_its_own_capacity_bucket() -> None:
    runtime_key = "agent-runtime-test-service-key-with-at-least-32-bytes"
    app = create_app(
        Settings(
            app_env="test",
            rate_limit_enabled=True,
            rate_limit_requests=1,
            rate_limit_window_seconds=30,
            agent_runtime_service_api_key=runtime_key,
            agent_runtime_rate_limit_requests=2,
            agent_runtime_rate_limit_window_seconds=10,
        )
    )

    with TestClient(app) as client:
        fake_redis = FakeRedis()
        app.state.redis_client = fake_redis
        headers = {"X-Service-Key": runtime_key}
        first = client.get(
            "/v1/internal/agent/not-a-route",
            headers=headers,
        )
        second = client.get(
            "/v1/internal/agent/not-a-route",
            headers=headers,
        )
        third = client.get(
            "/v1/internal/agent/not-a-route",
            headers=headers,
        )

    assert first.status_code == 404
    assert second.status_code == 404
    assert third.status_code == 429
    assert first.headers["X-RateLimit-Limit"] == "2"
    assert third.headers["Retry-After"] == "10"
    assert len(fake_redis.values) == 1
    assert next(iter(fake_redis.values)).startswith(
        "rate-limit:agent-runtime:"
    )


def test_invalid_runtime_service_key_does_not_receive_internal_capacity() -> None:
    app = create_app(
        Settings(
            app_env="test",
            rate_limit_enabled=True,
            rate_limit_requests=1,
            rate_limit_window_seconds=30,
            agent_runtime_service_api_key=(
                "agent-runtime-test-service-key-with-at-least-32-bytes"
            ),
            agent_runtime_rate_limit_requests=100,
        )
    )

    with TestClient(app) as client:
        app.state.redis_client = FakeRedis()
        headers = {"X-Service-Key": "invalid-service-key"}
        first = client.get(
            "/v1/internal/agent/not-a-route",
            headers=headers,
        )
        second = client.get(
            "/v1/internal/agent/not-a-route",
            headers=headers,
        )

    assert first.status_code == 404
    assert second.status_code == 429
    assert first.headers["X-RateLimit-Limit"] == "1"


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    async def eval(self, script, numkeys, key, window):
        assert "EXPIRE" in script and numkeys == 1
        count = await self.incr(key)
        await self.expire(key, window)
        return count

    async def incr(self, name: str) -> int:
        self.values[name] = self.values.get(name, 0) + 1
        return self.values[name]

    async def expire(self, name: str, time: int) -> bool:
        return name in self.values and time > 0
