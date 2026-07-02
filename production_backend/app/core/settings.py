from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse


LOCAL_DATABASE_URL = "postgresql+asyncpg://momcozy:momcozy@localhost:5432/momcozy"
LOCAL_REDIS_URL = "redis://localhost:6379/0"
LOCAL_OBJECT_STORAGE_ROOT = "production_backend/.local/object_storage"
SUPPORTED_OBJECT_STORAGE_PROVIDERS = {"local", "s3", "oss", "cos", "minio"}
PRODUCTION_ENVS = {"prod", "production"}
SUPPORTED_AUTH_JWT_ALGORITHMS = {"HS256"}


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
    auth_jwt_secret: str = ""
    auth_jwt_issuer: str = ""
    auth_jwt_audience: str = ""
    auth_jwt_algorithm: str = "HS256"
    service_api_key: str = ""
    readiness_check_infrastructure: bool = False

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
            auth_jwt_secret=_env("AUTH_JWT_SECRET", cls.auth_jwt_secret),
            auth_jwt_issuer=_env("AUTH_JWT_ISSUER", cls.auth_jwt_issuer),
            auth_jwt_audience=_env("AUTH_JWT_AUDIENCE", cls.auth_jwt_audience),
            auth_jwt_algorithm=_env("AUTH_JWT_ALGORITHM", cls.auth_jwt_algorithm),
            service_api_key=_env("SERVICE_API_KEY", cls.service_api_key),
            readiness_check_infrastructure=_env_bool(
                "READINESS_CHECK_INFRASTRUCTURE",
                cls.readiness_check_infrastructure,
            ),
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
        if self.auth_jwt_algorithm not in SUPPORTED_AUTH_JWT_ALGORITHMS:
            errors.append(
                "AUTH_JWT_ALGORITHM must be one of "
                f"{', '.join(sorted(SUPPORTED_AUTH_JWT_ALGORITHMS))}"
            )
        if self.auth_jwt_secret and len(self.auth_jwt_secret.encode("utf-8")) < 32:
            errors.append("AUTH_JWT_SECRET must be at least 32 bytes")
        if self.service_api_key and len(self.service_api_key.encode("utf-8")) < 32:
            errors.append("SERVICE_API_KEY must be at least 32 bytes")

        if self.is_production:
            if _is_local_url(self.database_url, LOCAL_DATABASE_URL):
                errors.append("DATABASE_URL must be explicitly configured for production")
            if _is_local_url(self.redis_url, LOCAL_REDIS_URL):
                errors.append("REDIS_URL must be explicitly configured for production")
            if provider == "local":
                errors.append("OBJECT_STORAGE_PROVIDER cannot be local in production")
            if provider != "local" and not self.object_storage_bucket:
                errors.append("OBJECT_STORAGE_BUCKET is required for managed object storage")
            if provider in {"minio", "oss", "cos"} and not self.object_storage_endpoint_url:
                errors.append("OBJECT_STORAGE_ENDPOINT_URL is required for this object storage provider")
            if provider != "local" and not self.object_storage_access_key_id:
                errors.append("OBJECT_STORAGE_ACCESS_KEY_ID is required for managed object storage")
            if provider != "local" and not self.object_storage_secret_access_key:
                errors.append("OBJECT_STORAGE_SECRET_ACCESS_KEY is required for managed object storage")
            if not self.auth_jwt_secret:
                errors.append("AUTH_JWT_SECRET is required in production")

        if errors:
            raise ValueError("; ".join(errors))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _is_local_url(value: str, local_default: str) -> bool:
    if value == local_default:
        return True
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    return hostname in {"localhost", "127.0.0.1", "::1"}
