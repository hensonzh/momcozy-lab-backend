import pytest

from production_backend.app.core.settings import Settings


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


def test_production_settings_reject_local_object_storage() -> None:
    settings = Settings(app_env="production", object_storage_provider="local")

    with pytest.raises(ValueError, match="OBJECT_STORAGE_PROVIDER cannot be local"):
        settings.validate_for_startup()


def test_production_managed_object_storage_requires_bucket_and_credentials() -> None:
    settings = Settings(app_env="production", object_storage_provider="oss")

    with pytest.raises(ValueError, match="OBJECT_STORAGE_BUCKET"):
        settings.validate_for_startup()
