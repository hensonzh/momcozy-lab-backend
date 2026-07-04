import pytest

from production_backend.app.core.settings import Settings


SERVICE_KEY = "service-key-value-with-at-least-32-bytes"


def test_settings_from_env_reads_infrastructure_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://example")
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/1")
    monkeypatch.setenv("OBJECT_STORAGE_PROVIDER", "s3")
    monkeypatch.setenv("OBJECT_STORAGE_BUCKET", "momcozy-staging")
    monkeypatch.setenv("OBJECT_STORAGE_REGION", "us-west-2")
    monkeypatch.setenv("OBJECT_STORAGE_ENDPOINT_URL", "https://s3.example.test")
    monkeypatch.setenv("OBJECT_STORAGE_ACCESS_KEY_ID", "access-key")
    monkeypatch.setenv("OBJECT_STORAGE_SECRET_ACCESS_KEY", "secret-key")

    settings = Settings.from_env()

    assert settings.app_env == "staging"
    assert settings.database_url == "postgresql+psycopg://example"
    assert settings.redis_url == "redis://example:6379/1"
    assert settings.object_storage_provider == "s3"
    assert settings.object_storage_bucket == "momcozy-staging"
    assert settings.object_storage_endpoint_url == "https://s3.example.test"


def test_settings_from_env_reads_file_upload_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FILE_UPLOAD_MAX_BYTES", "12345")

    settings = Settings.from_env()

    assert settings.file_upload_max_bytes == 12345


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
    monkeypatch.setenv("METRICS_REQUIRE_SERVICE_KEY", "true")

    settings = Settings.from_env()

    assert settings.rate_limit_enabled is True
    assert settings.rate_limit_requests == 42
    assert settings.rate_limit_window_seconds == 15
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


def test_settings_from_env_reads_agent_worker_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_RUNTIME_WORKER_ENABLED", "true")
    monkeypatch.setenv("AGENT_RUNTIME_WORKER_BATCH_LIMIT", "25")
    monkeypatch.setenv("AGENT_RUNTIME_WORKER_CONCURRENCY", "4")
    monkeypatch.setenv("AGENT_RUNTIME_WORKER_IDLE_SECONDS", "5")
    monkeypatch.setenv("AGENT_RUNTIME_RECOVER_RUNNING_OLDER_THAN_SECONDS", "120")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    monkeypatch.setenv("OPENAI_AGENT_MAX_TURNS", "7")
    monkeypatch.setenv("OPENAI_AGENT_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("OPENAI_AGENT_TRACE_ENABLED", "true")
    monkeypatch.setenv("OPENAI_AGENT_PROMPT_VERSION", "prompt-v2")

    settings = Settings.from_env()

    assert settings.agent_runtime_worker_enabled is True
    assert settings.agent_runtime_worker_batch_limit == 25
    assert settings.agent_runtime_worker_concurrency == 4
    assert settings.agent_runtime_worker_idle_seconds == 5
    assert settings.agent_runtime_recover_running_older_than_seconds == 120
    assert settings.openai_api_key == "sk-test"
    assert settings.openai_model == "gpt-test"
    assert settings.openai_agent_max_turns == 7
    assert settings.openai_agent_timeout_seconds == 45
    assert settings.openai_agent_trace_enabled is True
    assert settings.openai_agent_prompt_version == "prompt-v2"


def test_settings_from_env_reads_voice_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_PROVIDER", "local_stub")

    settings = Settings.from_env()

    assert settings.voice_provider == "local_stub"


def test_settings_from_env_reads_vision_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISION_PROVIDER", "local_stub")

    settings = Settings.from_env()

    assert settings.vision_provider == "local_stub"


def test_settings_from_env_reads_active_session_auth_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTH_REQUIRE_ACTIVE_SESSION", "true")

    settings = Settings.from_env()

    assert settings.auth_require_active_session is True


def test_settings_from_env_reads_outbox_worker_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTBOX_WORKER_ENABLED", "true")
    monkeypatch.setenv("OUTBOX_WORKER_IDLE_SECONDS", "4")
    monkeypatch.setenv("OUTBOX_WORKER_LEASE_SECONDS", "90")

    settings = Settings.from_env()

    assert settings.outbox_worker_enabled is True
    assert settings.outbox_worker_idle_seconds == 4
    assert settings.outbox_worker_lease_seconds == 90


def test_settings_reject_invalid_agent_worker_controls() -> None:
    settings = Settings(agent_runtime_worker_batch_limit=0)

    with pytest.raises(ValueError, match="AGENT_RUNTIME_WORKER_BATCH_LIMIT"):
        settings.validate_for_startup()

    settings = Settings(agent_runtime_worker_concurrency=0)

    with pytest.raises(ValueError, match="AGENT_RUNTIME_WORKER_CONCURRENCY"):
        settings.validate_for_startup()


def test_settings_requires_openai_key_when_agent_worker_is_enabled() -> None:
    settings = Settings(agent_runtime_worker_enabled=True, openai_api_key="")

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        settings.validate_for_startup()


def test_settings_reject_invalid_openai_agent_controls() -> None:
    with pytest.raises(ValueError, match="OPENAI_AGENT_MAX_TURNS"):
        Settings(openai_agent_max_turns=0).validate_for_startup()
    with pytest.raises(ValueError, match="OPENAI_AGENT_TIMEOUT_SECONDS"):
        Settings(openai_agent_timeout_seconds=0).validate_for_startup()
    with pytest.raises(ValueError, match="OPENAI_AGENT_PROMPT_VERSION"):
        Settings(openai_agent_prompt_version="x" * 81).validate_for_startup()


def test_settings_reject_invalid_outbox_worker_controls() -> None:
    settings = Settings(outbox_worker_lease_seconds=0)

    with pytest.raises(ValueError, match="OUTBOX_WORKER_LEASE_SECONDS"):
        settings.validate_for_startup()


def test_settings_reject_invalid_rate_limit_controls() -> None:
    settings = Settings(rate_limit_requests=0)

    with pytest.raises(ValueError, match="RATE_LIMIT_REQUESTS"):
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


def test_settings_reject_invalid_vision_provider() -> None:
    settings = Settings(vision_provider="legacy")

    with pytest.raises(ValueError, match="VISION_PROVIDER"):
        settings.validate_for_startup()


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
        auth_jwt_secret="test-secret-value-with-at-least-32-bytes",
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
        auth_jwt_secret="test-secret-value-with-at-least-32-bytes",
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
        auth_jwt_secret="test-secret-value-with-at-least-32-bytes",
        trusted_hosts=("api.example.test",),
    )

    with pytest.raises(ValueError, match="SERVICE_API_KEY"):
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
        auth_jwt_secret="test-secret-value-with-at-least-32-bytes",
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
        auth_jwt_secret="test-secret-value-with-at-least-32-bytes",
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
        auth_jwt_secret="test-secret-value-with-at-least-32-bytes",
        service_api_key=SERVICE_KEY,
        trusted_hosts=("api.example.test",),
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
        auth_jwt_secret="test-secret-value-with-at-least-32-bytes",
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
        auth_jwt_secret="test-secret-value-with-at-least-32-bytes",
        trusted_hosts=("api.example.test",),
        vision_provider="local_stub",
    )

    with pytest.raises(ValueError, match="VISION_PROVIDER=local_stub"):
        settings.validate_for_startup()
