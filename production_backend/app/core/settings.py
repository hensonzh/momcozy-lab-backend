from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse


LOCAL_DATABASE_URL = "postgresql+asyncpg://momcozy:momcozy@localhost:5432/momcozy"
LOCAL_REDIS_URL = "redis://localhost:6379/0"
LOCAL_OBJECT_STORAGE_ROOT = "production_backend/.local/object_storage"
DEFAULT_FILE_UPLOAD_MAX_BYTES = 10 * 1024 * 1024
SUPPORTED_OBJECT_STORAGE_PROVIDERS = {"local", "s3", "oss", "cos", "minio"}
PRODUCTION_ENVS = {"prod", "production"}
SUPPORTED_AUTH_JWT_ALGORITHMS = {"HS256"}
SUPPORTED_VOICE_PROVIDERS = {"disabled", "local_stub"}


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
    file_upload_max_bytes: int = DEFAULT_FILE_UPLOAD_MAX_BYTES
    auth_jwt_secret: str = ""
    auth_jwt_issuer: str = ""
    auth_jwt_audience: str = ""
    auth_jwt_algorithm: str = "HS256"
    auth_require_active_session: bool = False
    service_api_key: str = ""
    readiness_check_infrastructure: bool = False
    cors_allowed_origins: tuple[str, ...] = ()
    trusted_hosts: tuple[str, ...] = ()
    postgres_backup_hook: str = ""
    postgres_restore_hook: str = ""
    object_storage_backup_hook: str = ""
    object_storage_restore_hook: str = ""
    rate_limit_enabled: bool = False
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60
    metrics_require_service_key: bool = False
    agent_runtime_worker_enabled: bool = False
    agent_runtime_worker_batch_limit: int = 10
    agent_runtime_worker_idle_seconds: int = 2
    agent_runtime_recover_running_older_than_seconds: int = 900
    outbox_worker_enabled: bool = False
    outbox_worker_idle_seconds: int = 2
    outbox_worker_lease_seconds: int = 60
    openai_api_key: str = ""
    openai_model: str = "gpt-5.5"
    voice_provider: str = "disabled"
    log_level: str = "INFO"

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
            file_upload_max_bytes=_env_int("FILE_UPLOAD_MAX_BYTES", cls.file_upload_max_bytes),
            auth_jwt_secret=_env("AUTH_JWT_SECRET", cls.auth_jwt_secret),
            auth_jwt_issuer=_env("AUTH_JWT_ISSUER", cls.auth_jwt_issuer),
            auth_jwt_audience=_env("AUTH_JWT_AUDIENCE", cls.auth_jwt_audience),
            auth_jwt_algorithm=_env("AUTH_JWT_ALGORITHM", cls.auth_jwt_algorithm),
            auth_require_active_session=_env_bool("AUTH_REQUIRE_ACTIVE_SESSION", cls.auth_require_active_session),
            service_api_key=_env("SERVICE_API_KEY", cls.service_api_key),
            readiness_check_infrastructure=_env_bool(
                "READINESS_CHECK_INFRASTRUCTURE",
                cls.readiness_check_infrastructure,
            ),
            cors_allowed_origins=_env_csv("CORS_ALLOWED_ORIGINS", cls.cors_allowed_origins),
            trusted_hosts=_env_csv("TRUSTED_HOSTS", cls.trusted_hosts),
            postgres_backup_hook=_env("POSTGRES_BACKUP_HOOK", cls.postgres_backup_hook),
            postgres_restore_hook=_env("POSTGRES_RESTORE_HOOK", cls.postgres_restore_hook),
            object_storage_backup_hook=_env("OBJECT_STORAGE_BACKUP_HOOK", cls.object_storage_backup_hook),
            object_storage_restore_hook=_env("OBJECT_STORAGE_RESTORE_HOOK", cls.object_storage_restore_hook),
            rate_limit_enabled=_env_bool("RATE_LIMIT_ENABLED", cls.rate_limit_enabled),
            rate_limit_requests=_env_int("RATE_LIMIT_REQUESTS", cls.rate_limit_requests),
            rate_limit_window_seconds=_env_int("RATE_LIMIT_WINDOW_SECONDS", cls.rate_limit_window_seconds),
            metrics_require_service_key=_env_bool("METRICS_REQUIRE_SERVICE_KEY", cls.metrics_require_service_key),
            agent_runtime_worker_enabled=_env_bool("AGENT_RUNTIME_WORKER_ENABLED", cls.agent_runtime_worker_enabled),
            agent_runtime_worker_batch_limit=_env_int("AGENT_RUNTIME_WORKER_BATCH_LIMIT", cls.agent_runtime_worker_batch_limit),
            agent_runtime_worker_idle_seconds=_env_int("AGENT_RUNTIME_WORKER_IDLE_SECONDS", cls.agent_runtime_worker_idle_seconds),
            agent_runtime_recover_running_older_than_seconds=_env_int(
                "AGENT_RUNTIME_RECOVER_RUNNING_OLDER_THAN_SECONDS",
                cls.agent_runtime_recover_running_older_than_seconds,
            ),
            outbox_worker_enabled=_env_bool("OUTBOX_WORKER_ENABLED", cls.outbox_worker_enabled),
            outbox_worker_idle_seconds=_env_int("OUTBOX_WORKER_IDLE_SECONDS", cls.outbox_worker_idle_seconds),
            outbox_worker_lease_seconds=_env_int("OUTBOX_WORKER_LEASE_SECONDS", cls.outbox_worker_lease_seconds),
            openai_api_key=_env("OPENAI_API_KEY", cls.openai_api_key),
            openai_model=_env("OPENAI_MODEL", cls.openai_model),
            voice_provider=_env("VOICE_PROVIDER", cls.voice_provider).lower(),
            log_level=_env("LOG_LEVEL", cls.log_level).upper(),
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
        if self.file_upload_max_bytes < 1:
            errors.append("FILE_UPLOAD_MAX_BYTES must be positive")
        if self.log_level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            errors.append("LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")
        if self.agent_runtime_worker_batch_limit < 1:
            errors.append("AGENT_RUNTIME_WORKER_BATCH_LIMIT must be positive")
        if self.agent_runtime_worker_idle_seconds < 0:
            errors.append("AGENT_RUNTIME_WORKER_IDLE_SECONDS must be non-negative")
        if self.agent_runtime_recover_running_older_than_seconds < 1:
            errors.append("AGENT_RUNTIME_RECOVER_RUNNING_OLDER_THAN_SECONDS must be positive")
        if self.outbox_worker_idle_seconds < 0:
            errors.append("OUTBOX_WORKER_IDLE_SECONDS must be non-negative")
        if self.outbox_worker_lease_seconds < 1:
            errors.append("OUTBOX_WORKER_LEASE_SECONDS must be positive")
        if self.rate_limit_requests < 1:
            errors.append("RATE_LIMIT_REQUESTS must be positive")
        if self.rate_limit_window_seconds < 1:
            errors.append("RATE_LIMIT_WINDOW_SECONDS must be positive")
        if self.metrics_require_service_key and not self.service_api_key:
            errors.append("SERVICE_API_KEY is required when METRICS_REQUIRE_SERVICE_KEY is true")
        if self.agent_runtime_worker_enabled and not self.openai_api_key:
            errors.append("OPENAI_API_KEY is required when AGENT_RUNTIME_WORKER_ENABLED is true")
        if not self.openai_model:
            errors.append("OPENAI_MODEL is required")
        if self.voice_provider not in SUPPORTED_VOICE_PROVIDERS:
            errors.append(
                "VOICE_PROVIDER must be one of "
                f"{', '.join(sorted(SUPPORTED_VOICE_PROVIDERS))}"
            )

        if self.is_production:
            if _is_local_url(self.database_url, LOCAL_DATABASE_URL):
                errors.append("DATABASE_URL must be explicitly configured for production")
            if _is_local_url(self.redis_url, LOCAL_REDIS_URL):
                errors.append("REDIS_URL must be explicitly configured for production")
            if provider == "local":
                errors.append("OBJECT_STORAGE_PROVIDER cannot be local in production")
            if not self.trusted_hosts:
                errors.append("TRUSTED_HOSTS is required in production")
            if "*" in self.trusted_hosts:
                errors.append("TRUSTED_HOSTS cannot include * in production")
            if "*" in self.cors_allowed_origins:
                errors.append("CORS_ALLOWED_ORIGINS cannot include * in production")
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
            if not self.service_api_key:
                errors.append("SERVICE_API_KEY is required in production for protected operational endpoints")
            if self.voice_provider == "local_stub":
                errors.append("VOICE_PROVIDER=local_stub cannot be used in production")

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
    raise ValueError(f"{name} must be a boolean value: true/false")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None


def _env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default
    values = tuple(value.strip() for value in raw.split(",") if value.strip())
    return values


def _is_local_url(value: str, local_default: str) -> bool:
    if value == local_default:
        return True
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    return hostname in {"localhost", "127.0.0.1", "::1"}
