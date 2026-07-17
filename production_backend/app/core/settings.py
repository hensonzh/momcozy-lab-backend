from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from math import isfinite
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


LOCAL_DATABASE_URL = "postgresql+asyncpg://momcozy:momcozy@localhost:5432/momcozy"
LOCAL_REDIS_URL = "redis://localhost:6379/0"
LOCAL_OBJECT_STORAGE_ROOT = "production_backend/.local/object_storage"
LOCAL_PRODUCT_ASSET_MANIFEST_PATH = "production_backend/assets/product-assets.manifest.json"
DEFAULT_FILE_UPLOAD_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_AGENT_RUNTIME_MAX_INLINE_PAYLOAD_BYTES = 32 * 1024
DEFAULT_DOUBAO_TTS_WS_URL = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
DEFAULT_DOUBAO_TTS_RESOURCE_ID = "seed-tts-2.0"
DEFAULT_DOUBAO_TTS_VOICE_TYPE = "saturn_zh_female_qingyingduoduo_cs_tob"
DEFAULT_DOUBAO_TTS_AUDIO_FORMAT = "pcm"
DEFAULT_DOUBAO_TTS_SAMPLE_RATE = 24000
DEFAULT_DOUBAO_TTS_SPEED_RATIO = 1.1
DEFAULT_DOUBAO_TTS_FIRST_CHUNK_TIMEOUT_SECONDS = 20
SUPPORTED_OBJECT_STORAGE_PROVIDERS = {"local", "s3", "oss", "cos", "minio"}
PRODUCTION_ENVS = {"prod", "production"}
SUPPORTED_AUTH_JWT_ALGORITHMS = {"HS256"}
SUPPORTED_VOICE_PROVIDERS = {"disabled", "local_stub", "doubao", "volcengine"}
SUPPORTED_VISION_PROVIDERS = {"disabled", "local_stub", "openai"}
SUPPORTED_OPENAI_REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh", "max"}


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
    product_asset_manifest_path: str = LOCAL_PRODUCT_ASSET_MANIFEST_PATH
    product_asset_local_root: str = ""
    file_upload_max_bytes: int = DEFAULT_FILE_UPLOAD_MAX_BYTES
    auth_jwt_secret: str = ""
    auth_jwt_issuer: str = ""
    auth_jwt_audience: str = ""
    auth_jwt_algorithm: str = "HS256"
    auth_require_active_session: bool = False
    auth_invite_codes: tuple[str, ...] = ("MOMCOZY-BETA",)
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
    agent_runtime_worker_concurrency: int = 1
    agent_runtime_worker_idle_seconds: float = 0.1
    agent_runtime_interrupt_running_older_than_seconds: int = 900
    agent_runtime_max_inline_payload_bytes: int = DEFAULT_AGENT_RUNTIME_MAX_INLINE_PAYLOAD_BYTES
    outbox_worker_enabled: bool = False
    outbox_worker_idle_seconds: int = 2
    outbox_worker_lease_seconds: int = 60
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-terra"
    openai_reasoning_effort: str = "low"
    openai_responses_store: bool = False
    openai_agent_use_responses: bool = True
    openai_agent_max_turns: int = 10
    openai_agent_timeout_seconds: int = 60
    openai_agent_trace_enabled: bool = False
    openai_agent_prompt_version: str = "momcozy-agent-prompt-v3"
    agent_quick_reply_model: str = "gpt-5.4-nano"
    agent_quick_reply_timeout_seconds: float = 3.0
    agent_fact_extraction_enabled: bool = True
    agent_fact_extraction_model: str = "gpt-5.4-nano"
    agent_fact_extraction_timeout_seconds: float = 5.0
    agent_fact_extraction_version: str = "turn-fact-extractor-v2"
    agent_fact_worker_concurrency: int = 2
    agent_fact_worker_batch_limit: int = 10
    agent_fact_worker_idle_seconds: float = 0.5
    agent_fact_worker_lease_seconds: int = 30
    agent_fact_worker_max_attempts: int = 3
    agent_memory_consolidation_enabled: bool = False
    agent_memory_consolidation_model: str = "gpt-5.4-nano"
    agent_memory_consolidation_timeout_seconds: float = 30.0
    agent_memory_consolidation_timezone: str = "Asia/Shanghai"
    agent_memory_consolidation_hour: int = 3
    agent_memory_consolidation_max_users: int = 500
    agent_memory_consolidation_message_limit: int = 200
    agent_memory_consolidation_extractor_version: str = "memory-extractor-v1"
    voice_provider: str = "disabled"
    voice_api_key: str = ""
    voice_app_id: str = ""
    voice_access_key: str = ""
    voice_base_url: str = DEFAULT_DOUBAO_TTS_WS_URL
    voice_transcribe_model: str = ""
    voice_tts_resource_id: str = DEFAULT_DOUBAO_TTS_RESOURCE_ID
    voice_tts_voice_type: str = DEFAULT_DOUBAO_TTS_VOICE_TYPE
    voice_tts_audio_format: str = DEFAULT_DOUBAO_TTS_AUDIO_FORMAT
    voice_tts_sample_rate: int = DEFAULT_DOUBAO_TTS_SAMPLE_RATE
    voice_tts_speed_ratio: float = DEFAULT_DOUBAO_TTS_SPEED_RATIO
    voice_tts_first_chunk_timeout_seconds: int = DEFAULT_DOUBAO_TTS_FIRST_CHUNK_TIMEOUT_SECONDS
    voice_realtime_model: str = ""
    voice_request_timeout_seconds: int = 30
    vision_provider: str = "disabled"
    vision_openai_model: str = "gpt-5.4-mini"
    vision_request_timeout_seconds: float = 20.0
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
            product_asset_manifest_path=_env("PRODUCT_ASSET_MANIFEST_PATH", cls.product_asset_manifest_path),
            product_asset_local_root=_env("PRODUCT_ASSET_LOCAL_ROOT", cls.product_asset_local_root),
            file_upload_max_bytes=_env_int("FILE_UPLOAD_MAX_BYTES", cls.file_upload_max_bytes),
            auth_jwt_secret=_env("AUTH_JWT_SECRET", cls.auth_jwt_secret),
            auth_jwt_issuer=_env("AUTH_JWT_ISSUER", cls.auth_jwt_issuer),
            auth_jwt_audience=_env("AUTH_JWT_AUDIENCE", cls.auth_jwt_audience),
            auth_jwt_algorithm=_env("AUTH_JWT_ALGORITHM", cls.auth_jwt_algorithm),
            auth_require_active_session=_env_bool("AUTH_REQUIRE_ACTIVE_SESSION", cls.auth_require_active_session),
            auth_invite_codes=_env_csv("AUTH_INVITE_CODES", cls.auth_invite_codes),
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
            agent_runtime_worker_concurrency=_env_int("AGENT_RUNTIME_WORKER_CONCURRENCY", cls.agent_runtime_worker_concurrency),
            agent_runtime_worker_idle_seconds=_env_float("AGENT_RUNTIME_WORKER_IDLE_SECONDS", cls.agent_runtime_worker_idle_seconds),
            agent_runtime_interrupt_running_older_than_seconds=_env_int_first(
                ("AGENT_RUNTIME_INTERRUPT_RUNNING_OLDER_THAN_SECONDS", "AGENT_RUNTIME_RECOVER_RUNNING_OLDER_THAN_SECONDS"),
                cls.agent_runtime_interrupt_running_older_than_seconds,
            ),
            agent_runtime_max_inline_payload_bytes=_env_int(
                "AGENT_RUNTIME_MAX_INLINE_PAYLOAD_BYTES",
                cls.agent_runtime_max_inline_payload_bytes,
            ),
            outbox_worker_enabled=_env_bool("OUTBOX_WORKER_ENABLED", cls.outbox_worker_enabled),
            outbox_worker_idle_seconds=_env_int("OUTBOX_WORKER_IDLE_SECONDS", cls.outbox_worker_idle_seconds),
            outbox_worker_lease_seconds=_env_int("OUTBOX_WORKER_LEASE_SECONDS", cls.outbox_worker_lease_seconds),
            openai_api_key=_env("OPENAI_API_KEY", cls.openai_api_key),
            openai_model=_env("OPENAI_MODEL", cls.openai_model),
            openai_reasoning_effort=_env("OPENAI_REASONING_EFFORT", cls.openai_reasoning_effort).lower(),
            openai_responses_store=_env_bool("OPENAI_RESPONSES_STORE", cls.openai_responses_store),
            openai_agent_use_responses=_env_bool("OPENAI_AGENT_USE_RESPONSES", cls.openai_agent_use_responses),
            openai_agent_max_turns=_env_int("OPENAI_AGENT_MAX_TURNS", cls.openai_agent_max_turns),
            openai_agent_timeout_seconds=_env_int("OPENAI_AGENT_TIMEOUT_SECONDS", cls.openai_agent_timeout_seconds),
            openai_agent_trace_enabled=_env_bool("OPENAI_AGENT_TRACE_ENABLED", cls.openai_agent_trace_enabled),
            openai_agent_prompt_version=_env("OPENAI_AGENT_PROMPT_VERSION", cls.openai_agent_prompt_version),
            agent_quick_reply_model=_env("AGENT_QUICK_REPLY_MODEL", cls.agent_quick_reply_model),
            agent_quick_reply_timeout_seconds=_env_float(
                "AGENT_QUICK_REPLY_TIMEOUT_SECONDS",
                cls.agent_quick_reply_timeout_seconds,
            ),
            agent_fact_extraction_enabled=_env_bool(
                "AGENT_FACT_EXTRACTION_ENABLED",
                cls.agent_fact_extraction_enabled,
            ),
            agent_fact_extraction_model=_env(
                "AGENT_FACT_EXTRACTION_MODEL",
                cls.agent_fact_extraction_model,
            ),
            agent_fact_extraction_timeout_seconds=_env_float(
                "AGENT_FACT_EXTRACTION_TIMEOUT_SECONDS",
                cls.agent_fact_extraction_timeout_seconds,
            ),
            agent_fact_extraction_version=_env(
                "AGENT_FACT_EXTRACTION_VERSION",
                cls.agent_fact_extraction_version,
            ),
            agent_fact_worker_concurrency=_env_int(
                "AGENT_FACT_WORKER_CONCURRENCY",
                cls.agent_fact_worker_concurrency,
            ),
            agent_fact_worker_batch_limit=_env_int(
                "AGENT_FACT_WORKER_BATCH_LIMIT",
                cls.agent_fact_worker_batch_limit,
            ),
            agent_fact_worker_idle_seconds=_env_float(
                "AGENT_FACT_WORKER_IDLE_SECONDS",
                cls.agent_fact_worker_idle_seconds,
            ),
            agent_fact_worker_lease_seconds=_env_int(
                "AGENT_FACT_WORKER_LEASE_SECONDS",
                cls.agent_fact_worker_lease_seconds,
            ),
            agent_fact_worker_max_attempts=_env_int(
                "AGENT_FACT_WORKER_MAX_ATTEMPTS",
                cls.agent_fact_worker_max_attempts,
            ),
            agent_memory_consolidation_enabled=_env_bool(
                "AGENT_MEMORY_CONSOLIDATION_ENABLED",
                cls.agent_memory_consolidation_enabled,
            ),
            agent_memory_consolidation_model=_env(
                "AGENT_MEMORY_CONSOLIDATION_MODEL",
                cls.agent_memory_consolidation_model,
            ),
            agent_memory_consolidation_timeout_seconds=_env_float(
                "AGENT_MEMORY_CONSOLIDATION_TIMEOUT_SECONDS",
                cls.agent_memory_consolidation_timeout_seconds,
            ),
            agent_memory_consolidation_timezone=_env(
                "AGENT_MEMORY_CONSOLIDATION_TIMEZONE",
                cls.agent_memory_consolidation_timezone,
            ),
            agent_memory_consolidation_hour=_env_int(
                "AGENT_MEMORY_CONSOLIDATION_HOUR",
                cls.agent_memory_consolidation_hour,
            ),
            agent_memory_consolidation_max_users=_env_int(
                "AGENT_MEMORY_CONSOLIDATION_MAX_USERS",
                cls.agent_memory_consolidation_max_users,
            ),
            agent_memory_consolidation_message_limit=_env_int(
                "AGENT_MEMORY_CONSOLIDATION_MESSAGE_LIMIT",
                cls.agent_memory_consolidation_message_limit,
            ),
            agent_memory_consolidation_extractor_version=_env(
                "AGENT_MEMORY_CONSOLIDATION_EXTRACTOR_VERSION",
                cls.agent_memory_consolidation_extractor_version,
            ),
            voice_provider=_env("VOICE_PROVIDER", cls.voice_provider).lower(),
            voice_api_key=_env_first(
                ("VOICE_API_KEY", "VOLC_TTS_API_KEY", "VOLC_REALTIME_VOICE_API_KEY", "VOLCENGINE_TTS_API_KEY"),
                cls.voice_api_key,
            ),
            voice_app_id=_env_first(("VOICE_APP_ID", "VOLC_TTS_APP_ID", "VOLC_REALTIME_VOICE_APP_ID"), cls.voice_app_id),
            voice_access_key=_env_first(
                (
                    "VOICE_ACCESS_KEY",
                    "VOLC_TTS_ACCESS_TOKEN",
                    "VOLC_TTS_ACCESS_KEY",
                    "VOLC_REALTIME_VOICE_ACCESS_TOKEN",
                    "VOLC_REALTIME_VOICE_ACCESS_KEY",
                ),
                cls.voice_access_key,
            ),
            voice_base_url=_env_first(("VOICE_BASE_URL", "VOLC_TTS_WS_URL"), cls.voice_base_url),
            voice_transcribe_model=_env("VOICE_TRANSCRIBE_MODEL", cls.voice_transcribe_model),
            voice_tts_resource_id=_env_first(
                ("VOICE_TTS_RESOURCE_ID", "VOICE_TTS_MODEL", "VOLC_TTS_RESOURCE_ID"),
                cls.voice_tts_resource_id,
            ),
            voice_tts_voice_type=_env_first(("VOICE_TTS_VOICE_TYPE", "VOLC_TTS_VOICE_TYPE"), cls.voice_tts_voice_type),
            voice_tts_audio_format=_env_first(("VOICE_TTS_AUDIO_FORMAT", "VOLC_TTS_AUDIO_FORMAT"), cls.voice_tts_audio_format).lower(),
            voice_tts_sample_rate=_env_int_first(("VOICE_TTS_SAMPLE_RATE", "VOLC_TTS_SAMPLE_RATE"), cls.voice_tts_sample_rate),
            voice_tts_speed_ratio=_env_float_first(("VOICE_TTS_SPEED_RATIO", "VOLC_TTS_SPEED_RATIO"), cls.voice_tts_speed_ratio),
            voice_tts_first_chunk_timeout_seconds=_env_int(
                "VOICE_TTS_FIRST_CHUNK_TIMEOUT_SECONDS",
                cls.voice_tts_first_chunk_timeout_seconds,
            ),
            voice_realtime_model=_env("VOICE_REALTIME_MODEL", cls.voice_realtime_model),
            voice_request_timeout_seconds=_env_int(
                "VOICE_REQUEST_TIMEOUT_SECONDS",
                cls.voice_request_timeout_seconds,
            ),
            vision_provider=_env("VISION_PROVIDER", cls.vision_provider).lower(),
            vision_openai_model=_env("VISION_OPENAI_MODEL", cls.vision_openai_model),
            vision_request_timeout_seconds=_env_float(
                "VISION_REQUEST_TIMEOUT_SECONDS",
                cls.vision_request_timeout_seconds,
            ),
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
            errors.append(f"OBJECT_STORAGE_PROVIDER must be one of {', '.join(sorted(SUPPORTED_OBJECT_STORAGE_PROVIDERS))}")
        if self.auth_jwt_algorithm not in SUPPORTED_AUTH_JWT_ALGORITHMS:
            errors.append(f"AUTH_JWT_ALGORITHM must be one of {', '.join(sorted(SUPPORTED_AUTH_JWT_ALGORITHMS))}")
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
        if self.agent_runtime_worker_concurrency < 1:
            errors.append("AGENT_RUNTIME_WORKER_CONCURRENCY must be positive")
        if self.agent_runtime_worker_idle_seconds < 0:
            errors.append("AGENT_RUNTIME_WORKER_IDLE_SECONDS must be non-negative")
        if self.agent_runtime_interrupt_running_older_than_seconds < 1:
            errors.append("AGENT_RUNTIME_INTERRUPT_RUNNING_OLDER_THAN_SECONDS must be positive")
        if self.agent_runtime_max_inline_payload_bytes < 1:
            errors.append("AGENT_RUNTIME_MAX_INLINE_PAYLOAD_BYTES must be positive")
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
        if (self.agent_runtime_worker_enabled or self.agent_memory_consolidation_enabled) and not self.openai_api_key:
            errors.append("OPENAI_API_KEY is required when the agent runtime or memory consolidation worker is enabled")
        if not self.openai_model:
            errors.append("OPENAI_MODEL is required")
        if self.openai_reasoning_effort not in SUPPORTED_OPENAI_REASONING_EFFORTS:
            errors.append("OPENAI_REASONING_EFFORT must be one of " + ", ".join(sorted(SUPPORTED_OPENAI_REASONING_EFFORTS)))
        if self.openai_agent_max_turns < 1:
            errors.append("OPENAI_AGENT_MAX_TURNS must be positive")
        if self.openai_agent_timeout_seconds < 1:
            errors.append("OPENAI_AGENT_TIMEOUT_SECONDS must be positive")
        if not self.openai_agent_prompt_version:
            errors.append("OPENAI_AGENT_PROMPT_VERSION is required")
        if len(self.openai_agent_prompt_version) > 80:
            errors.append("OPENAI_AGENT_PROMPT_VERSION must be at most 80 characters")
        if self.agent_quick_reply_timeout_seconds <= 0:
            errors.append("AGENT_QUICK_REPLY_TIMEOUT_SECONDS must be positive")
        if self.agent_fact_extraction_enabled and not self.agent_fact_extraction_model:
            errors.append("AGENT_FACT_EXTRACTION_MODEL is required when fact extraction is enabled")
        if not isfinite(self.agent_fact_extraction_timeout_seconds) or self.agent_fact_extraction_timeout_seconds <= 0:
            errors.append("AGENT_FACT_EXTRACTION_TIMEOUT_SECONDS must be finite and positive")
        if not self.agent_fact_extraction_version or len(self.agent_fact_extraction_version) > 80:
            errors.append("AGENT_FACT_EXTRACTION_VERSION must be between 1 and 80 characters")
        if self.agent_fact_worker_concurrency < 1:
            errors.append("AGENT_FACT_WORKER_CONCURRENCY must be positive")
        if self.agent_fact_worker_batch_limit < 1:
            errors.append("AGENT_FACT_WORKER_BATCH_LIMIT must be positive")
        if not isfinite(self.agent_fact_worker_idle_seconds) or self.agent_fact_worker_idle_seconds < 0:
            errors.append("AGENT_FACT_WORKER_IDLE_SECONDS must be finite and non-negative")
        if self.agent_fact_worker_lease_seconds < 1:
            errors.append("AGENT_FACT_WORKER_LEASE_SECONDS must be positive")
        elif self.agent_fact_worker_lease_seconds <= self.agent_fact_extraction_timeout_seconds:
            errors.append(
                "AGENT_FACT_WORKER_LEASE_SECONDS must be greater than AGENT_FACT_EXTRACTION_TIMEOUT_SECONDS"
            )
        if self.agent_fact_worker_max_attempts < 1:
            errors.append("AGENT_FACT_WORKER_MAX_ATTEMPTS must be positive")
        if self.agent_runtime_worker_enabled and self.agent_fact_extraction_enabled and not self.openai_api_key:
            errors.append("OPENAI_API_KEY is required when agent fact extraction is enabled")
        if self.agent_memory_consolidation_enabled and not self.agent_memory_consolidation_model:
            errors.append("AGENT_MEMORY_CONSOLIDATION_MODEL is required when memory consolidation is enabled")
        if self.agent_memory_consolidation_timeout_seconds <= 0:
            errors.append("AGENT_MEMORY_CONSOLIDATION_TIMEOUT_SECONDS must be positive")
        if self.agent_memory_consolidation_hour < 0 or self.agent_memory_consolidation_hour > 23:
            errors.append("AGENT_MEMORY_CONSOLIDATION_HOUR must be between 0 and 23")
        if self.agent_memory_consolidation_max_users < 1:
            errors.append("AGENT_MEMORY_CONSOLIDATION_MAX_USERS must be positive")
        if self.agent_memory_consolidation_message_limit < 1 or self.agent_memory_consolidation_message_limit > 1000:
            errors.append("AGENT_MEMORY_CONSOLIDATION_MESSAGE_LIMIT must be between 1 and 1000")
        if not self.agent_memory_consolidation_extractor_version:
            errors.append("AGENT_MEMORY_CONSOLIDATION_EXTRACTOR_VERSION is required")
        elif len(self.agent_memory_consolidation_extractor_version) > 80:
            errors.append("AGENT_MEMORY_CONSOLIDATION_EXTRACTOR_VERSION must be at most 80 characters")
        try:
            ZoneInfo(self.agent_memory_consolidation_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            errors.append("AGENT_MEMORY_CONSOLIDATION_TIMEZONE must be a valid IANA timezone")
        if self.voice_provider not in SUPPORTED_VOICE_PROVIDERS:
            errors.append(f"VOICE_PROVIDER must be one of {', '.join(sorted(SUPPORTED_VOICE_PROVIDERS))}")
        if self.voice_provider in {"doubao", "volcengine"} and not self.voice_api_key and not (self.voice_app_id and self.voice_access_key):
            errors.append("VOICE_API_KEY or VOICE_APP_ID + VOICE_ACCESS_KEY is required when VOICE_PROVIDER=doubao")
        if not self.voice_base_url:
            errors.append("VOICE_BASE_URL is required")
        if not self.voice_tts_resource_id:
            errors.append("VOICE_TTS_RESOURCE_ID is required")
        if not self.voice_tts_voice_type:
            errors.append("VOICE_TTS_VOICE_TYPE is required")
        if not self.voice_tts_audio_format:
            errors.append("VOICE_TTS_AUDIO_FORMAT is required")
        if self.voice_tts_audio_format != "pcm":
            errors.append("VOICE_TTS_AUDIO_FORMAT must be pcm")
        if self.voice_tts_sample_rate < 1:
            errors.append("VOICE_TTS_SAMPLE_RATE must be positive")
        if self.voice_tts_speed_ratio < 0.5 or self.voice_tts_speed_ratio > 2.0:
            errors.append("VOICE_TTS_SPEED_RATIO must be between 0.5 and 2.0")
        if self.voice_tts_first_chunk_timeout_seconds < 1:
            errors.append("VOICE_TTS_FIRST_CHUNK_TIMEOUT_SECONDS must be positive")
        if self.voice_request_timeout_seconds < 1:
            errors.append("VOICE_REQUEST_TIMEOUT_SECONDS must be positive")
        if self.vision_provider not in SUPPORTED_VISION_PROVIDERS:
            errors.append(f"VISION_PROVIDER must be one of {', '.join(sorted(SUPPORTED_VISION_PROVIDERS))}")
        if self.vision_provider == "openai" and not self.openai_api_key:
            errors.append("OPENAI_API_KEY is required when VISION_PROVIDER=openai")
        if self.vision_provider == "openai" and not self.vision_openai_model:
            errors.append("VISION_OPENAI_MODEL is required when VISION_PROVIDER=openai")
        if not isfinite(self.vision_request_timeout_seconds) or self.vision_request_timeout_seconds <= 0:
            errors.append("VISION_REQUEST_TIMEOUT_SECONDS must be positive")

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
            if self.vision_provider == "local_stub":
                errors.append("VISION_PROVIDER=local_stub cannot be used in production")

        if errors:
            raise ValueError("; ".join(errors))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default


def _env_first(names: tuple[str, ...], default: str) -> str:
    for name in names:
        raw = os.getenv(name)
        if raw is not None and raw.strip():
            return raw.strip()
    return default


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


def _env_int_first(names: tuple[str, ...], default: int) -> int:
    for name in names:
        raw = os.getenv(name)
        if raw is not None:
            try:
                return int(raw.strip())
            except ValueError:
                raise ValueError(f"{name} must be an integer") from None
    return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        raise ValueError(f"{name} must be a number") from None


def _env_float_first(names: tuple[str, ...], default: float) -> float:
    for name in names:
        raw = os.getenv(name)
        if raw is not None:
            try:
                return float(raw.strip())
            except ValueError:
                raise ValueError(f"{name} must be a number") from None
    return default


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
