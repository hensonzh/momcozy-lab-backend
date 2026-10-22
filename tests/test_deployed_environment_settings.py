import pytest

from app.core.settings import Settings


def test_only_staging_is_a_deployed_environment() -> None:
    assert Settings(app_env="staging").is_deployed is True
    assert Settings(app_env="local").is_deployed is False
    assert Settings(app_env="test").is_deployed is False


def test_removed_production_environment_fails_closed() -> None:
    with pytest.raises(ValueError, match="APP_ENV"):
        Settings(app_env="production").validate_for_startup()


def test_unknown_environment_fails_closed() -> None:
    with pytest.raises(ValueError, match="APP_ENV"):
        Settings(app_env="prod-like").validate_for_startup()


def test_staging_rejects_local_infrastructure_defaults() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings(app_env="staging").validate_for_startup()
