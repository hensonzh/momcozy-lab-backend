from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from app.core.backup_restore import (
    build_backup_restore_manifest,
    validate_backup_restore_hooks,
)
from app.core.settings import Settings
from tests.auth_key_material import TEST_RSA_PRIVATE_KEY_B64


def test_non_production_backup_restore_hooks_are_documented_but_not_required() -> None:
    manifest = build_backup_restore_manifest(Settings(app_env="local"))

    assert manifest.missing_required == ()
    assert manifest.redis_backup_required is False
    assert {hook.env_var for hook in manifest.hooks} == {
        "POSTGRES_BACKUP_HOOK",
        "POSTGRES_RESTORE_HOOK",
        "OBJECT_STORAGE_BACKUP_HOOK",
        "OBJECT_STORAGE_RESTORE_HOOK",
    }


def test_production_managed_infrastructure_requires_backup_and_restore_hooks() -> None:
    settings = _production_managed_settings()

    manifest = build_backup_restore_manifest(settings)

    assert manifest.missing_required == (
        "POSTGRES_BACKUP_HOOK",
        "POSTGRES_RESTORE_HOOK",
        "OBJECT_STORAGE_BACKUP_HOOK",
        "OBJECT_STORAGE_RESTORE_HOOK",
    )
    with pytest.raises(ValueError, match="POSTGRES_BACKUP_HOOK"):
        validate_backup_restore_hooks(settings)


def test_production_managed_infrastructure_accepts_configured_hooks() -> None:
    settings = _production_managed_settings(
        postgres_backup_hook="pg-backup-job",
        postgres_restore_hook="pg-restore-job",
        object_storage_backup_hook="objects-backup-job",
        object_storage_restore_hook="objects-restore-job",
    )

    manifest = build_backup_restore_manifest(settings)

    assert manifest.missing_required == ()
    validate_backup_restore_hooks(settings)


def test_backup_restore_hook_script_strict_mode_fails_for_missing_production_hooks() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_backup_restore_hooks.py",
            "--strict",
        ],
        cwd=_repo_root(),
        env=_script_env(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert "POSTGRES_BACKUP_HOOK" in payload["missing_required"]
    assert "OBJECT_STORAGE_RESTORE_HOOK" in payload["missing_required"]


def _production_managed_settings(**overrides: str) -> Settings:
    values = {
        "app_env": "production",
        "database_url": "postgresql+asyncpg://app:secret@postgres.internal:5432/momcozy",
        "redis_url": "redis://redis.internal:6379/0",
        "object_storage_provider": "s3",
        "object_storage_bucket": "momcozy-prod",
        "object_storage_access_key_id": "access",
        "object_storage_secret_access_key": "secret",
        "auth_jwt_private_key_b64": TEST_RSA_PRIVATE_KEY_B64,
        "auth_jwt_issuer": "momcozy-test",
        "auth_jwt_product_audience": "momcozy-product-api",
        "auth_jwt_runtime_audience": "momcozy-agent-runtime",
    }
    values.update(overrides)
    return Settings(**values)


def _script_env() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(_repo_root()),
        "APP_ENV": "production",
        "DATABASE_URL": "postgresql+asyncpg://app:secret@postgres.internal:5432/momcozy",
        "REDIS_URL": "redis://redis.internal:6379/0",
        "OBJECT_STORAGE_PROVIDER": "s3",
        "OBJECT_STORAGE_BUCKET": "momcozy-prod",
        "OBJECT_STORAGE_ACCESS_KEY_ID": "access",
        "OBJECT_STORAGE_SECRET_ACCESS_KEY": "secret",
        "AUTH_JWT_PRIVATE_KEY_B64": TEST_RSA_PRIVATE_KEY_B64,
        "AUTH_JWT_ISSUER": "momcozy-test",
        "AUTH_JWT_PRODUCT_AUDIENCE": "momcozy-product-api",
        "AUTH_JWT_RUNTIME_AUDIENCE": "momcozy-agent-runtime",
    }


def _repo_root() -> str:
    return os.fspath(os.path.dirname(os.path.dirname(__file__)))
