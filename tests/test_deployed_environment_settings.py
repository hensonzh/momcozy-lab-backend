import pytest

from app.core.settings import Settings


def test_staging_and_production_are_deployed_environments() -> None:
    assert Settings(app_env="staging").is_deployed is True
    assert Settings(app_env="production").is_deployed is True
    assert Settings(app_env="local").is_deployed is False
    assert Settings(app_env="test").is_deployed is False


def test_unknown_environment_fails_closed() -> None:
    with pytest.raises(ValueError, match="APP_ENV"):
        Settings(app_env="prod-like").validate_for_startup()


def test_staging_rejects_local_infrastructure_defaults() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL"):
        Settings(app_env="staging").validate_for_startup()
