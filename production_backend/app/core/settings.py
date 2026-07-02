from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


LOCAL_DATABASE_URL = "postgresql+psycopg://momcozy:momcozy@localhost:5432/momcozy"
LOCAL_REDIS_URL = "redis://localhost:6379/0"
LOCAL_OBJECT_STORAGE_ROOT = "production_backend/.local/object_storage"
SUPPORTED_OBJECT_STORAGE_PROVIDERS = {"local", "s3", "oss", "cos", "minio"}
PRODUCTION_ENVS = {"prod", "production"}


@dataclass(frozen=True)
class Settings:
    app_name: str = "MomCozy Production Backend"
    app_version: str = "0.1.0"
    app_env: str = "local"
    database_url: str = LOCAL_DATABASE_URL
    redis_url: str = LOCAL_REDIS_URL
    object_storage_provider: str = "local"
    object_storage_bucket: str = ""
    object_storage_region: str = ""
    object_storage_endpoint_url: str = ""
    object_storage_access_key_id: str = ""
    object_storage_secret_access_key: str = ""
    object_storage_local_root: str = LOCAL_OBJECT_STORAGE_ROOT

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            app_name=_env("APP_NAME", cls.app_name),
            app_version=_env("APP_VERSION", cls.app_version),
            app_env=_env("APP_ENV", cls.app_env),
            database_url=_env("DATABASE_URL", cls.database_url),
            redis_url=_env("REDIS_URL", cls.redis_url),
            object_storage_provider=_env("OBJECT_STORAGE_PROVIDER", cls.object_storage_provider).lower(),
            object_storage_bucket=_env("OBJECT_STORAGE_BUCKET", cls.object_storage_bucket),
            object_storage_region=_env("OBJECT_STORAGE_REGION", cls.object_storage_region),
            object_storage_endpoint_url=_env("OBJECT_STORAGE_ENDPOINT_URL", cls.object_storage_endpoint_url),
            object_storage_access_key_id=_env("OBJECT_STORAGE_ACCESS_KEY_ID", cls.object_storage_access_key_id),
            object_storage_secret_access_key=_env("OBJECT_STORAGE_SECRET_ACCESS_KEY", cls.object_storage_secret_access_key),
            object_storage_local_root=_env("OBJECT_STORAGE_LOCAL_ROOT", cls.object_storage_local_root),
        )

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in PRODUCTION_ENVS

    def validate_for_startup(self) -> None:
        errors: list[str] = []
        provider = self.object_storage_provider.lower()

        if not self.database_url:
            errors.append("DATABASE_URL is required")
        if not self.redis_url:
            errors.append("REDIS_URL is required")
        if provider not in SUPPORTED_OBJECT_STORAGE_PROVIDERS:
            errors.append(
                "OBJECT_STORAGE_PROVIDER must be one of "
                f"{', '.join(sorted(SUPPORTED_OBJECT_STORAGE_PROVIDERS))}"
            )

        if self.is_production:
            if provider == "local":
                errors.append("OBJECT_STORAGE_PROVIDER cannot be local in production")
            if provider != "local" and not self.object_storage_bucket:
                errors.append("OBJECT_STORAGE_BUCKET is required for managed object storage")
            if provider != "local" and not self.object_storage_access_key_id:
                errors.append("OBJECT_STORAGE_ACCESS_KEY_ID is required for managed object storage")
            if provider != "local" and not self.object_storage_secret_access_key:
                errors.append("OBJECT_STORAGE_SECRET_ACCESS_KEY is required for managed object storage")

        if errors:
            raise ValueError("; ".join(errors))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default
