from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.factory import create_app
from tests.auth_key_material import TEST_RSA_PRIVATE_KEY_B64


SERVICE_KEY = "service-key-value-with-at-least-32-bytes"
AGENT_RUNTIME_SERVICE_KEY = "agent-runtime-service-key-with-at-least-32-bytes"


def test_create_app_uses_injected_settings() -> None:
    app = create_app(Settings(app_name="Test Backend", app_version="9.9.9", app_env="test"))

    assert app.title == "Test Backend"
    assert app.version == "9.9.9"


def test_health_endpoints_are_registered() -> None:
    client = TestClient(create_app(Settings(app_env="test")))

    assert client.get("/v1/health/live").json() == {"status": "ok"}
    assert client.get("/v1/health/ready").json() == {"status": "ok"}


def test_security_headers_are_added_to_responses() -> None:
    response = TestClient(create_app(Settings(app_env="test"))).get("/v1/health/live")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Strict-Transport-Security" not in response.headers


def test_hsts_header_is_added_in_production() -> None:
    response = TestClient(create_app(_production_settings())).get("/v1/health/live")

    assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"


def test_trusted_host_middleware_rejects_untrusted_hosts() -> None:
    client = TestClient(create_app(Settings(app_env="test", trusted_hosts=("api.example.test",))))

    response = client.get("/v1/health/live", headers={"Host": "evil.example.test"})

    assert response.status_code == 400
    assert response.text == "Invalid host header"
    assert response.headers["X-Request-ID"]


def test_trusted_host_middleware_allows_configured_hosts() -> None:
    client = TestClient(create_app(Settings(app_env="test", trusted_hosts=("api.example.test",))))

    response = client.get("/v1/health/live", headers={"Host": "api.example.test"})

    assert response.status_code == 200


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


def _production_settings() -> Settings:
    return Settings(
        app_env="production",
        database_url="postgresql+asyncpg://app:secret@postgres.internal:5432/momcozy",
        redis_url="redis://redis.internal:6379/0",
        object_storage_provider="s3",
        object_storage_bucket="bucket",
        object_storage_access_key_id="access",
        object_storage_secret_access_key="secret",
        auth_jwt_private_key_b64=TEST_RSA_PRIVATE_KEY_B64,
        auth_jwt_issuer="momcozy-test",
        auth_jwt_product_audience="momcozy-product-api",
        auth_jwt_runtime_audience="momcozy-agent-runtime",
        service_api_key=SERVICE_KEY,
        agent_runtime_service_api_key=AGENT_RUNTIME_SERVICE_KEY,
        agent_model_asset_public_base_url="https://api.example.test",
        trusted_hosts=("testserver",),
    )
