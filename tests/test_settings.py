import pytest

from app.core.settings import Settings
from tests.auth_key_material import TEST_RSA_1024_PRIVATE_KEY_B64, TEST_RSA_PRIVATE_KEY_B64, auth_settings


SERVICE_KEY = "service-key-value-with-at-least-32-bytes"
AGENT_RUNTIME_SERVICE_KEY = "agent-runtime-service-key-with-at-least-32-bytes"


def test_settings_from_env_reads_asymmetric_jwt_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_JWT_PRIVATE_KEY_B64", TEST_RSA_PRIVATE_KEY_B64)
    monkeypatch.setenv("AUTH_JWT_ISSUER", "https://auth.example.test")
    monkeypatch.setenv("AUTH_JWT_PRODUCT_AUDIENCE", "product-api")
    monkeypatch.setenv("AUTH_JWT_RUNTIME_AUDIENCE", "runtime-api")

    settings = Settings.from_env()

    assert settings.auth_jwt_private_key_b64 == TEST_RSA_PRIVATE_KEY_B64
    assert settings.auth_jwt_issuer == "https://auth.example.test"
    assert settings.auth_jwt_product_audience == "product-api"
    assert settings.auth_jwt_runtime_audience == "runtime-api"


@pytest.mark.parametrize(
    ("private_key", "expected_error"),
    [
        ("not-base64", "AUTH_JWT_PRIVATE_KEY_B64 must be valid base64"),
        ("bm90IGEgcHJpdmF0ZSBrZXk=", "AUTH_JWT_PRIVATE_KEY_B64 must contain an unencrypted PKCS#8 RSA private key"),
        (TEST_RSA_1024_PRIVATE_KEY_B64, "AUTH_JWT_PRIVATE_KEY_B64 RSA key must be at least 2048 bits"),
    ],
)
def test_settings_reject_invalid_jwt_private_key(private_key: str, expected_error: str) -> None:
    settings = auth_settings(auth_jwt_private_key_b64=private_key)

    with pytest.raises(ValueError, match=expected_error):
        settings.validate_for_startup()


def test_settings_reject_matching_product_and_runtime_audiences() -> None:
    settings = auth_settings(auth_jwt_runtime_audience="momcozy-product-api")

    with pytest.raises(ValueError, match="must differ"):
        settings.validate_for_startup()


def test_settings_accept_valid_asymmetric_jwt_configuration() -> None:
    auth_settings().validate_for_startup()


def test_settings_repr_does_not_expose_jwt_private_key() -> None:
    settings = auth_settings()

    assert TEST_RSA_PRIVATE_KEY_B64 not in repr(settings)
    assert "auth_jwt_private_key_b64" not in repr(settings)


def test_settings_use_current_vision_defaults() -> None:
    settings = Settings()

    assert settings.vision_openai_model == "gpt-5.4-mini"
    assert settings.vision_request_timeout_seconds == 20.0


def test_settings_use_sliding_agent_model_asset_defaults() -> None:
    settings = Settings()

    assert settings.agent_model_asset_public_base_url == ""
    assert settings.agent_model_asset_inactivity_ttl_seconds == 1800


def test_settings_from_env_reads_infrastructure_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://example")
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/1")
    monkeypatch.setenv("OBJECT_STORAGE_PROVIDER", "s3")
    monkeypatch.setenv("OBJECT_STORAGE_BUCKET", "momcozy-staging")
    monkeypatch.setenv("OBJECT_STORAGE_REGION", "us-west-2")
    monkeypatch.setenv("OBJECT_STORAGE_ENDPOINT_URL", "https://s3.example.test")
    monkeypatch.setenv("OBJECT_STORAGE_PUBLIC_ENDPOINT_URL", "https://images.example.test")
    monkeypatch.setenv("OBJECT_STORAGE_ACCESS_KEY_ID", "access-key")
    monkeypatch.setenv("OBJECT_STORAGE_SECRET_ACCESS_KEY", "secret-key")
    monkeypatch.setenv("PRODUCT_ASSET_MANIFEST_PATH", "/etc/momcozy/product-assets.manifest.json")
    monkeypatch.setenv("PRODUCT_ASSET_LOCAL_ROOT", "/tmp/momcozy-assets")

    settings = Settings.from_env()

    assert settings.app_env == "staging"
    assert settings.database_url == "postgresql+psycopg://example"
    assert settings.redis_url == "redis://example:6379/1"
    assert settings.object_storage_provider == "s3"
    assert settings.object_storage_bucket == "momcozy-staging"
    assert settings.object_storage_endpoint_url == "https://s3.example.test"
    assert settings.object_storage_public_endpoint_url == "https://images.example.test"
    assert settings.product_asset_manifest_path == "/etc/momcozy/product-assets.manifest.json"
    assert settings.product_asset_local_root == "/tmp/momcozy-assets"


def test_settings_from_env_reads_file_upload_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FILE_UPLOAD_MAX_BYTES", "12345")

    settings = Settings.from_env()

    assert settings.file_upload_max_bytes == 12345


def test_settings_from_env_reads_agent_runtime_service_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_RUNTIME_SERVICE_API_KEY", AGENT_RUNTIME_SERVICE_KEY)

    settings = Settings.from_env()

    assert settings.agent_runtime_service_api_key == AGENT_RUNTIME_SERVICE_KEY


def test_settings_from_env_reads_agent_model_asset_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "AGENT_MODEL_ASSET_PUBLIC_BASE_URL",
        "https://api.example.test",
    )
    monkeypatch.setenv(
        "AGENT_MODEL_ASSET_INACTIVITY_TTL_SECONDS",
        "1800",
    )

    settings = Settings.from_env()

    assert (
        settings.agent_model_asset_public_base_url
        == "https://api.example.test"
    )
    assert settings.agent_model_asset_inactivity_ttl_seconds == 1800


def test_settings_reject_unbounded_agent_model_asset_inactivity_ttl() -> None:
    settings = Settings(
        agent_model_asset_inactivity_ttl_seconds=86_401,
    )

    with pytest.raises(
        ValueError,
        match="AGENT_MODEL_ASSET_INACTIVITY_TTL_SECONDS",
    ):
        settings.validate_for_startup()


def test_settings_reject_insecure_agent_model_asset_origin() -> None:
    settings = Settings(
        agent_model_asset_public_base_url="http://api.internal",
    )

    with pytest.raises(
        ValueError,
        match="AGENT_MODEL_ASSET_PUBLIC_BASE_URL",
    ):
        settings.validate_for_startup()


def test_settings_reject_insecure_public_object_storage_endpoint() -> None:
    settings = Settings(object_storage_public_endpoint_url="http://images.example.test")

    with pytest.raises(ValueError, match="OBJECT_STORAGE_PUBLIC_ENDPOINT_URL"):
        settings.validate_for_startup()


def test_settings_from_env_rejects_invalid_integer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FILE_UPLOAD_MAX_BYTES", "ten")

    with pytest.raises(ValueError, match="FILE_UPLOAD_MAX_BYTES must be an integer"):
        Settings.from_env()


def test_settings_from_env_reads_operational_hook_references(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_BACKUP_HOOK", "managed-postgres-backup")
    monkeypatch.setenv("POSTGRES_RESTORE_HOOK", "managed-postgres-restore")
    monkeypatch.setenv("OBJECT_STORAGE_BACKUP_HOOK", "managed-objects-backup")
    monkeypatch.setenv("OBJECT_STORAGE_RESTORE_HOOK", "managed-objects-restore")

    settings = Settings.from_env()

    assert settings.postgres_backup_hook == "managed-postgres-backup"
    assert settings.postgres_restore_hook == "managed-postgres-restore"
    assert settings.object_storage_backup_hook == "managed-objects-backup"
    assert settings.object_storage_restore_hook == "managed-objects-restore"


def test_settings_from_env_reads_rate_limit_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "42")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "15")
    monkeypatch.setenv("AGENT_RUNTIME_RATE_LIMIT_REQUESTS", "900")
    monkeypatch.setenv("AGENT_RUNTIME_RATE_LIMIT_WINDOW_SECONDS", "30")
    monkeypatch.setenv("METRICS_REQUIRE_SERVICE_KEY", "true")

    settings = Settings.from_env()

    assert settings.rate_limit_enabled is True
    assert settings.rate_limit_requests == 42
    assert settings.rate_limit_window_seconds == 15
    assert settings.agent_runtime_rate_limit_requests == 900
    assert settings.agent_runtime_rate_limit_window_seconds == 30
    assert settings.metrics_require_service_key is True


def test_settings_from_env_rejects_invalid_boolean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "maybe")

    with pytest.raises(ValueError, match="RATE_LIMIT_ENABLED must be a boolean"):
        Settings.from_env()


def test_settings_from_env_reads_cors_allowed_origins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://app.example.test, https://admin.example.test")

    settings = Settings.from_env()

    assert settings.cors_allowed_origins == ("https://app.example.test", "https://admin.example.test")


def test_settings_from_env_reads_trusted_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTED_HOSTS", "api.example.test,admin.example.test")

    settings = Settings.from_env()

    assert settings.trusted_hosts == ("api.example.test", "admin.example.test")


def test_settings_from_env_reads_voice_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_PROVIDER", "doubao")
    monkeypatch.setenv("VOICE_API_KEY", "voice-test-key")
    monkeypatch.setenv("VOICE_BASE_URL", "https://voice.example.test")
    monkeypatch.setenv("VOICE_TRANSCRIBE_MODEL", "voice-transcribe-test")
    monkeypatch.setenv("VOICE_TTS_RESOURCE_ID", "voice-resource-test")
    monkeypatch.setenv("VOICE_TTS_VOICE_TYPE", "voice-speaker-test")
    monkeypatch.setenv("VOICE_TTS_AUDIO_FORMAT", "pcm")
    monkeypatch.setenv("VOICE_TTS_SAMPLE_RATE", "16000")
    monkeypatch.setenv("VOICE_TTS_SPEED_RATIO", "1.2")
    monkeypatch.setenv("VOICE_TTS_FIRST_CHUNK_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("VOICE_REALTIME_MODEL", "voice-realtime-test")
    monkeypatch.setenv("VOICE_REQUEST_TIMEOUT_SECONDS", "45")

    settings = Settings.from_env()

    assert settings.voice_provider == "doubao"
    assert settings.voice_api_key == "voice-test-key"
    assert settings.voice_base_url == "https://voice.example.test"
    assert settings.voice_transcribe_model == "voice-transcribe-test"
    assert settings.voice_tts_resource_id == "voice-resource-test"
    assert settings.voice_tts_voice_type == "voice-speaker-test"
    assert settings.voice_tts_audio_format == "pcm"
    assert settings.voice_tts_sample_rate == 16000
    assert settings.voice_tts_speed_ratio == 1.2
    assert settings.voice_tts_first_chunk_timeout_seconds == 12
    assert settings.voice_realtime_model == "voice-realtime-test"
    assert settings.voice_request_timeout_seconds == 45


def test_settings_from_env_ignores_retired_volc_tts_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOLC_TTS_API_KEY", "legacy-volc-key")
    monkeypatch.setenv("VOLC_TTS_WS_URL", "wss://legacy.example.test/tts")
    monkeypatch.setenv("VOLC_TTS_RESOURCE_ID", "legacy-resource")
    monkeypatch.setenv("VOLC_TTS_VOICE_TYPE", "legacy-speaker")
    monkeypatch.setenv("VOLC_TTS_AUDIO_FORMAT", "pcm")
    monkeypatch.setenv("VOLC_TTS_SAMPLE_RATE", "24000")
    monkeypatch.setenv("VOLC_TTS_SPEED_RATIO", "1.1")

    settings = Settings.from_env()

    assert settings.voice_api_key == ""
    assert settings.voice_base_url == Settings.voice_base_url
    assert settings.voice_tts_resource_id == Settings.voice_tts_resource_id
    assert settings.voice_tts_voice_type == Settings.voice_tts_voice_type
    assert settings.voice_tts_audio_format == "pcm"
    assert settings.voice_tts_sample_rate == 24000
    assert settings.voice_tts_speed_ratio == 1.1


def test_settings_from_env_ignores_retired_volc_app_access_key_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOLC_TTS_APP_ID", "legacy-app-id")
    monkeypatch.setenv("VOLC_TTS_ACCESS_TOKEN", "legacy-access-token")

    settings = Settings.from_env()

    assert settings.voice_app_id == ""
    assert settings.voice_access_key == ""


def test_settings_from_env_reads_vision_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISION_PROVIDER", "openai")
    monkeypatch.setenv("VISION_OPENAI_MODEL", "gpt-vision-test")
    monkeypatch.setenv("VISION_REQUEST_TIMEOUT_SECONDS", "7.5")

    settings = Settings.from_env()

    assert settings.vision_provider == "openai"
    assert settings.vision_openai_model == "gpt-vision-test"
    assert settings.vision_request_timeout_seconds == 7.5


def test_settings_from_env_reads_active_session_auth_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_REQUIRE_ACTIVE_SESSION", "true")

    settings = Settings.from_env()

    assert settings.auth_require_active_session is True


def test_settings_do_not_expose_embedded_agent_runtime_controls() -> None:
    settings = Settings()

    for retired_field in (
        "agent_runtime_worker_enabled",
        "agent_runtime_max_inline_payload_bytes",
        "openai_model",
        "openai_agent_max_turns",
        "agent_quick_reply_model",
        "agent_fact_extraction_enabled",
        "agent_memory_consolidation_enabled",
    ):
        assert not hasattr(settings, retired_field)


def test_settings_reject_invalid_rate_limit_controls() -> None:
    settings = Settings(rate_limit_requests=0)

    with pytest.raises(ValueError, match="RATE_LIMIT_REQUESTS"):
        settings.validate_for_startup()


@pytest.mark.parametrize(
    "field",
    (
        "agent_runtime_rate_limit_requests",
        "agent_runtime_rate_limit_window_seconds",
    ),
)
def test_settings_reject_invalid_agent_runtime_rate_limit_controls(
    field: str,
) -> None:
    settings = Settings(**{field: 0})

    with pytest.raises(ValueError, match="AGENT_RUNTIME_RATE_LIMIT"):
        settings.validate_for_startup()


def test_metrics_service_key_gate_requires_configured_service_key() -> None:
    settings = Settings(metrics_require_service_key=True, service_api_key="")

    with pytest.raises(ValueError, match="METRICS_REQUIRE_SERVICE_KEY"):
        settings.validate_for_startup()


def test_settings_reject_invalid_file_upload_limit() -> None:
    settings = Settings(file_upload_max_bytes=0)

    with pytest.raises(ValueError, match="FILE_UPLOAD_MAX_BYTES"):
        settings.validate_for_startup()


def test_settings_reject_invalid_voice_provider() -> None:
    settings = Settings(voice_provider="legacy")

    with pytest.raises(ValueError, match="VOICE_PROVIDER"):
        settings.validate_for_startup()


def test_settings_require_doubao_voice_key_when_provider_enabled() -> None:
    settings = Settings(voice_provider="doubao", voice_api_key="")

    with pytest.raises(ValueError, match="VOICE_API_KEY"):
        settings.validate_for_startup()


def test_settings_reject_invalid_voice_timeout() -> None:
    settings = Settings(voice_request_timeout_seconds=0)

    with pytest.raises(ValueError, match="VOICE_REQUEST_TIMEOUT_SECONDS"):
        settings.validate_for_startup()


def test_settings_reject_invalid_voice_speed_ratio() -> None:
    settings = Settings(voice_tts_speed_ratio=2.5)

    with pytest.raises(ValueError, match="VOICE_TTS_SPEED_RATIO"):
        settings.validate_for_startup()


def test_settings_reject_invalid_vision_provider() -> None:
    settings = Settings(vision_provider="legacy")

    with pytest.raises(ValueError, match="VISION_PROVIDER"):
        settings.validate_for_startup()


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), float("inf")])
def test_settings_require_openai_vision_credentials_model_and_finite_positive_timeout(timeout: float) -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        Settings(vision_provider="openai", openai_api_key="").validate_for_startup()
    with pytest.raises(ValueError, match="VISION_OPENAI_MODEL"):
        Settings(vision_provider="openai", openai_api_key="test-key", vision_openai_model="").validate_for_startup()
    with pytest.raises(ValueError, match="VISION_REQUEST_TIMEOUT_SECONDS"):
        Settings(
            vision_provider="openai",
            openai_api_key="test-key",
            vision_request_timeout_seconds=timeout,
        ).validate_for_startup()


def test_production_settings_reject_local_object_storage() -> None:
    settings = Settings(app_env="production", object_storage_provider="local")

    with pytest.raises(ValueError, match="OBJECT_STORAGE_PROVIDER cannot be local"):
        settings.validate_for_startup()


def test_production_settings_reject_wildcard_cors_origin() -> None:
    settings = Settings(
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
        cors_allowed_origins=("*",),
        trusted_hosts=("api.example.test",),
    )

    with pytest.raises(ValueError, match="CORS_ALLOWED_ORIGINS"):
        settings.validate_for_startup()


def test_production_settings_require_trusted_hosts() -> None:
    settings = Settings(
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
    )

    with pytest.raises(ValueError, match="TRUSTED_HOSTS"):
        settings.validate_for_startup()


def test_production_settings_require_service_key_for_operational_endpoints() -> None:
    settings = Settings(
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
        agent_runtime_service_api_key=AGENT_RUNTIME_SERVICE_KEY,
        trusted_hosts=("api.example.test",),
    )

    with pytest.raises(ValueError, match="SERVICE_API_KEY"):
        settings.validate_for_startup()


def test_production_settings_require_agent_runtime_service_key() -> None:
    settings = Settings(
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
        trusted_hosts=("api.example.test",),
    )

    with pytest.raises(ValueError, match="AGENT_RUNTIME_SERVICE_API_KEY"):
        settings.validate_for_startup()


def test_production_settings_reject_wildcard_trusted_hosts() -> None:
    settings = Settings(
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
        trusted_hosts=("*",),
    )

    with pytest.raises(ValueError, match="TRUSTED_HOSTS cannot include"):
        settings.validate_for_startup()


def test_production_managed_object_storage_requires_bucket_and_credentials() -> None:
    settings = Settings(app_env="production", object_storage_provider="oss")

    with pytest.raises(ValueError, match="OBJECT_STORAGE_BUCKET"):
        settings.validate_for_startup()


def test_production_rejects_implicit_local_database_and_redis_urls() -> None:
    settings = Settings(
        app_env="production",
        object_storage_provider="s3",
        object_storage_bucket="bucket",
        object_storage_access_key_id="access",
        object_storage_secret_access_key="secret",
        auth_jwt_private_key_b64=TEST_RSA_PRIVATE_KEY_B64,
        auth_jwt_issuer="momcozy-test",
        auth_jwt_product_audience="momcozy-product-api",
        auth_jwt_runtime_audience="momcozy-agent-runtime",
    )

    with pytest.raises(ValueError, match="DATABASE_URL must be explicitly configured"):
        settings.validate_for_startup()


def test_production_accepts_explicit_managed_infrastructure_urls() -> None:
    settings = Settings(
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
        trusted_hosts=("api.example.test",),
    )

    settings.validate_for_startup()


def test_production_accepts_configured_openai_vision_provider() -> None:
    settings = Settings(
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
        trusted_hosts=("api.example.test",),
        vision_provider="openai",
        openai_api_key="test-key",
        vision_openai_model="gpt-vision-test",
    )

    settings.validate_for_startup()


def test_production_rejects_local_stub_voice_provider() -> None:
    settings = Settings(
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
        trusted_hosts=("api.example.test",),
        voice_provider="local_stub",
    )

    with pytest.raises(ValueError, match="VOICE_PROVIDER=local_stub"):
        settings.validate_for_startup()


def test_production_rejects_local_stub_vision_provider() -> None:
    settings = Settings(
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
        trusted_hosts=("api.example.test",),
        vision_provider="local_stub",
    )

    with pytest.raises(ValueError, match="VISION_PROVIDER=local_stub"):
        settings.validate_for_startup()
