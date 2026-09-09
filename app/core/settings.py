from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass, field
from functools import lru_cache
from math import isfinite
from urllib.parse import urlparse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


LOCAL_DATABASE_URL = "postgresql+asyncpg://momcozy:momcozy@localhost:5432/momcozy"
LOCAL_REDIS_URL = "redis://localhost:6379/0"
LOCAL_OBJECT_STORAGE_ROOT = ".local/object_storage"
LOCAL_PRODUCT_ASSET_MANIFEST_PATH = "assets/product-assets.manifest.json"
DEFAULT_FILE_UPLOAD_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_AGENT_MODEL_ASSET_INACTIVITY_TTL_SECONDS = 30 * 60
MAX_AGENT_MODEL_ASSET_INACTIVITY_TTL_SECONDS = 24 * 60 * 60
DEFAULT_DOUBAO_TTS_WS_URL = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
DEFAULT_DOUBAO_TTS_RESOURCE_ID = "seed-tts-2.0"
DEFAULT_DOUBAO_TTS_VOICE_TYPE = "saturn_zh_female_qingyingduoduo_cs_tob"
DEFAULT_DOUBAO_TTS_AUDIO_FORMAT = "pcm"
DEFAULT_DOUBAO_TTS_SAMPLE_RATE = 24000
DEFAULT_DOUBAO_TTS_SPEED_RATIO = 1.1
DEFAULT_DOUBAO_TTS_FIRST_CHUNK_TIMEOUT_SECONDS = 20
SUPPORTED_OBJECT_STORAGE_PROVIDERS = {"local", "s3", "oss", "cos", "minio"}
PRODUCTION_ENVS = {"prod", "production"}
SUPPORTED_VOICE_PROVIDERS = {"disabled", "local_stub", "doubao", "volcengine"}
SUPPORTED_VISION_PROVIDERS = {"disabled", "local_stub", "openai"}


@dataclass(frozen=True)
class Settings:
    app_name: str = "Product Backend"
    app_version: str = "0.1.0"
    app_env: str = "local"
    database_url: str = LOCAL_DATABASE_URL
    redis_url: str = LOCAL_REDIS_URL
    object_storage_provider: str = "local"
    object_storage_bucket: str = ""
    object_storage_region: str = ""
    object_storage_endpoint_url: str = ""
    object_storage_public_endpoint_url: str = ""
    object_storage_access_key_id: str = ""
    object_storage_secret_access_key: str = ""
    object_storage_local_root: str = LOCAL_OBJECT_STORAGE_ROOT
    product_asset_manifest_path: str = LOCAL_PRODUCT_ASSET_MANIFEST_PATH
    product_asset_local_root: str = ""
    file_upload_max_bytes: int = DEFAULT_FILE_UPLOAD_MAX_BYTES
    auth_jwt_private_key_b64: str = field(default="", repr=False)
    auth_jwt_issuer: str = ""
    auth_jwt_product_audience: str = ""
    auth_jwt_runtime_audience: str = ""
    auth_require_active_session: bool = False
    ibclc_mfa_encryption_key: str = field(default="", repr=False)
    ibclc_session_hours: int = 12
    auth_invite_codes: tuple[str, ...] = ("MOMCOZY-BETA",)
    service_api_key: str = ""
    agent_runtime_service_api_key: str = ""
    care_report_runtime_url: str = ''
    care_report_service_key: str = field(default='', repr=False)
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
    agent_runtime_rate_limit_requests: int = 6_000
    agent_runtime_rate_limit_window_seconds: int = 60
    metrics_require_service_key: bool = False
    agent_model_asset_public_base_url: str = ""
    agent_model_asset_inactivity_ttl_seconds: int = (
        DEFAULT_AGENT_MODEL_ASSET_INACTIVITY_TTL_SECONDS
    )
    openai_api_key: str = ""
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
    consultation_video_provider: str = "disabled"
    consultation_livekit_url: str = ""
    consultation_livekit_client_url: str = ""
    consultation_livekit_api_key: str = field(default="", repr=False)
    consultation_livekit_api_secret: str = field(default="", repr=False)
    consultation_demo_early_join: bool = False
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
            object_storage_public_endpoint_url=_env(
                "OBJECT_STORAGE_PUBLIC_ENDPOINT_URL",
                cls.object_storage_public_endpoint_url,
            ),
            object_storage_access_key_id=_env("OBJECT_STORAGE_ACCESS_KEY_ID", cls.object_storage_access_key_id),
            object_storage_secret_access_key=_env("OBJECT_STORAGE_SECRET_ACCESS_KEY", cls.object_storage_secret_access_key),
            object_storage_local_root=_env("OBJECT_STORAGE_LOCAL_ROOT", cls.object_storage_local_root),
            product_asset_manifest_path=_env("PRODUCT_ASSET_MANIFEST_PATH", cls.product_asset_manifest_path),
            product_asset_local_root=_env("PRODUCT_ASSET_LOCAL_ROOT", cls.product_asset_local_root),
            file_upload_max_bytes=_env_int("FILE_UPLOAD_MAX_BYTES", cls.file_upload_max_bytes),
            auth_jwt_private_key_b64=_env("AUTH_JWT_PRIVATE_KEY_B64", cls.auth_jwt_private_key_b64),
            auth_jwt_issuer=_env("AUTH_JWT_ISSUER", cls.auth_jwt_issuer),
            auth_jwt_product_audience=_env("AUTH_JWT_PRODUCT_AUDIENCE", cls.auth_jwt_product_audience),
            auth_jwt_runtime_audience=_env("AUTH_JWT_RUNTIME_AUDIENCE", cls.auth_jwt_runtime_audience),
            auth_require_active_session=_env_bool("AUTH_REQUIRE_ACTIVE_SESSION", cls.auth_require_active_session),
            auth_invite_codes=_env_csv("AUTH_INVITE_CODES", cls.auth_invite_codes),
            service_api_key=_env("SERVICE_API_KEY", cls.service_api_key),
            agent_runtime_service_api_key=_env(
                "AGENT_RUNTIME_SERVICE_API_KEY",
                cls.agent_runtime_service_api_key,
            ),
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
            agent_runtime_rate_limit_requests=_env_int(
                "AGENT_RUNTIME_RATE_LIMIT_REQUESTS",
                cls.agent_runtime_rate_limit_requests,
            ),
            agent_runtime_rate_limit_window_seconds=_env_int(
                "AGENT_RUNTIME_RATE_LIMIT_WINDOW_SECONDS",
                cls.agent_runtime_rate_limit_window_seconds,
            ),
            metrics_require_service_key=_env_bool("METRICS_REQUIRE_SERVICE_KEY", cls.metrics_require_service_key),
            agent_model_asset_public_base_url=_env(
                "AGENT_MODEL_ASSET_PUBLIC_BASE_URL",
                cls.agent_model_asset_public_base_url,
            ),
            agent_model_asset_inactivity_ttl_seconds=_env_int(
                "AGENT_MODEL_ASSET_INACTIVITY_TTL_SECONDS",
                cls.agent_model_asset_inactivity_ttl_seconds,
            ),
            openai_api_key=_env("OPENAI_API_KEY", cls.openai_api_key),
            voice_provider=_env("VOICE_PROVIDER", cls.voice_provider).lower(),
            voice_api_key=_env("VOICE_API_KEY", cls.voice_api_key),
            voice_app_id=_env("VOICE_APP_ID", cls.voice_app_id),
            voice_access_key=_env("VOICE_ACCESS_KEY", cls.voice_access_key),
            voice_base_url=_env("VOICE_BASE_URL", cls.voice_base_url),
            voice_transcribe_model=_env("VOICE_TRANSCRIBE_MODEL", cls.voice_transcribe_model),
            voice_tts_resource_id=_env("VOICE_TTS_RESOURCE_ID", cls.voice_tts_resource_id),
            voice_tts_voice_type=_env("VOICE_TTS_VOICE_TYPE", cls.voice_tts_voice_type),
            voice_tts_audio_format=_env("VOICE_TTS_AUDIO_FORMAT", cls.voice_tts_audio_format).lower(),
            voice_tts_sample_rate=_env_int("VOICE_TTS_SAMPLE_RATE", cls.voice_tts_sample_rate),
            voice_tts_speed_ratio=_env_float("VOICE_TTS_SPEED_RATIO", cls.voice_tts_speed_ratio),
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
            consultation_video_provider=_env("CONSULTATION_VIDEO_PROVIDER", cls.consultation_video_provider).lower(),
            consultation_livekit_url=_env("CONSULTATION_LIVEKIT_URL", cls.consultation_livekit_url),
            consultation_livekit_client_url=_env("CONSULTATION_LIVEKIT_CLIENT_URL", cls.consultation_livekit_client_url),
            consultation_livekit_api_key=_env("CONSULTATION_LIVEKIT_API_KEY", cls.consultation_livekit_api_key),
            consultation_livekit_api_secret=_env("CONSULTATION_LIVEKIT_API_SECRET", cls.consultation_livekit_api_secret),
            consultation_demo_early_join=_env_bool("CONSULTATION_DEMO_EARLY_JOIN", cls.consultation_demo_early_join),
            ibclc_mfa_encryption_key=_env("IBCLC_MFA_ENCRYPTION_KEY", cls.ibclc_mfa_encryption_key),
            ibclc_session_hours=_env_int("IBCLC_SESSION_HOURS", cls.ibclc_session_hours),
            care_report_runtime_url=_env('CARE_REPORT_RUNTIME_URL', cls.care_report_runtime_url).rstrip('/'),
            care_report_service_key=_env('CARE_REPORT_SERVICE_KEY', cls.care_report_service_key),
            log_level=_env("LOG_LEVEL", cls.log_level).upper(),
        )

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in PRODUCTION_ENVS

    def validate_for_startup(self) -> None:
        errors: list[str] = []
        if self.care_report_runtime_url:
            runtime = urlparse(self.care_report_runtime_url)
            try:
                valid_port = runtime.port is None or 1 <= runtime.port <= 65535
            except ValueError:
                valid_port = False
            if (not valid_port or not runtime.hostname or runtime.scheme not in {'http', 'https'} or
                runtime.username is not None or runtime.password is not None or runtime.path not in {'', '/'} or runtime.query or runtime.fragment):
                errors.append('CARE_REPORT_RUNTIME_URL must be an HTTP(S) origin without credentials')
            if self.is_production and runtime.scheme != 'https':
                errors.append('CARE_REPORT_RUNTIME_URL must use HTTPS in production')
        if self.care_report_service_key:
            if len(self.care_report_service_key.encode('utf-8')) < 32:
                errors.append('CARE_REPORT_SERVICE_KEY must be at least 32 bytes')
            if self.care_report_service_key in {self.service_api_key, self.agent_runtime_service_api_key}:
                errors.append('CARE_REPORT_SERVICE_KEY must be distinct from other service credentials')
        if not 1 <= self.ibclc_session_hours <= 24:
            errors.append("IBCLC_SESSION_HOURS must be between 1 and 24")
        if self.ibclc_mfa_encryption_key:
            from cryptography.fernet import Fernet
            try:
                Fernet(self.ibclc_mfa_encryption_key.encode("ascii"))
            except (ValueError, UnicodeError):
                errors.append("IBCLC_MFA_ENCRYPTION_KEY must be a valid Fernet key")
        provider = self.object_storage_provider.lower()
        if self.consultation_video_provider not in {"disabled", "sandbox", "livekit"}:
            errors.append("CONSULTATION_VIDEO_PROVIDER must be disabled, sandbox or livekit")
        if self.is_production and (self.consultation_video_provider == "sandbox" or self.consultation_demo_early_join):
            errors.append("Production consultations cannot use sandbox video or bypass the join window")
        if self.consultation_video_provider == "livekit":
            if not self.consultation_livekit_api_key or not self.consultation_livekit_api_secret:
                errors.append("LiveKit credentials are required")
            video_url = urlparse(self.consultation_livekit_url)
            if video_url.scheme not in {"ws", "wss"} or not video_url.netloc or video_url.username or video_url.password or video_url.query or video_url.fragment:
                errors.append("CONSULTATION_LIVEKIT_URL must be a WebSocket origin")
            if self.is_production and video_url.scheme != "wss":
                errors.append("Production LiveKit connections require WSS")
            client_url = urlparse(self.consultation_livekit_client_url or self.consultation_livekit_url)
            if client_url.scheme not in {"ws", "wss"} or not client_url.netloc or client_url.username or client_url.password or client_url.query or client_url.fragment:
                errors.append("CONSULTATION_LIVEKIT_CLIENT_URL must be a WebSocket origin")
            if self.is_production and client_url.scheme != "wss":
                errors.append("Production LiveKit client connections require WSS")

        if not self.database_url:
            errors.append("DATABASE_URL is required")
        if not self.redis_url:
            errors.append("REDIS_URL is required")
        if provider not in SUPPORTED_OBJECT_STORAGE_PROVIDERS:
            errors.append(f"OBJECT_STORAGE_PROVIDER must be one of {', '.join(sorted(SUPPORTED_OBJECT_STORAGE_PROVIDERS))}")
        jwt_configured = any(
            (
                self.auth_jwt_private_key_b64,
                self.auth_jwt_issuer,
                self.auth_jwt_product_audience,
                self.auth_jwt_runtime_audience,
            )
        )
        if jwt_configured:
            if not self.auth_jwt_private_key_b64:
                errors.append("AUTH_JWT_PRIVATE_KEY_B64 is required when authentication is configured")
            else:
                try:
                    load_auth_jwt_private_key(self.auth_jwt_private_key_b64)
                except ValueError as exc:
                    errors.append(str(exc))
            if not self.auth_jwt_issuer:
                errors.append("AUTH_JWT_ISSUER is required when authentication is configured")
            if not self.auth_jwt_product_audience:
                errors.append("AUTH_JWT_PRODUCT_AUDIENCE is required when authentication is configured")
            if not self.auth_jwt_runtime_audience:
                errors.append("AUTH_JWT_RUNTIME_AUDIENCE is required when authentication is configured")
            if (
                self.auth_jwt_product_audience
                and self.auth_jwt_runtime_audience
                and self.auth_jwt_product_audience == self.auth_jwt_runtime_audience
            ):
                errors.append("AUTH_JWT_PRODUCT_AUDIENCE and AUTH_JWT_RUNTIME_AUDIENCE must differ")
        if self.service_api_key and len(self.service_api_key.encode("utf-8")) < 32:
            errors.append("SERVICE_API_KEY must be at least 32 bytes")
        if self.agent_runtime_service_api_key and len(self.agent_runtime_service_api_key.encode("utf-8")) < 32:
            errors.append("AGENT_RUNTIME_SERVICE_API_KEY must be at least 32 bytes")
        if self.agent_runtime_service_api_key and self.agent_runtime_service_api_key == self.service_api_key:
            errors.append("AGENT_RUNTIME_SERVICE_API_KEY must differ from SERVICE_API_KEY")
        if self.file_upload_max_bytes < 1:
            errors.append("FILE_UPLOAD_MAX_BYTES must be positive")
        if self.log_level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            errors.append("LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")
        if not (
            60
            <= self.agent_model_asset_inactivity_ttl_seconds
            <= MAX_AGENT_MODEL_ASSET_INACTIVITY_TTL_SECONDS
        ):
            errors.append(
                "AGENT_MODEL_ASSET_INACTIVITY_TTL_SECONDS must be "
                "between 60 and 86400"
            )
        if self.agent_model_asset_public_base_url:
            model_asset_url = urlparse(
                self.agent_model_asset_public_base_url
            )
            if (
                model_asset_url.scheme != "https"
                or not model_asset_url.netloc
                or model_asset_url.username is not None
                or model_asset_url.password is not None
                or model_asset_url.query
                or model_asset_url.fragment
                or model_asset_url.path not in {"", "/"}
            ):
                errors.append(
                    "AGENT_MODEL_ASSET_PUBLIC_BASE_URL must be an "
                    "absolute HTTPS origin without path, query, or fragment"
                )
        if self.object_storage_public_endpoint_url:
            public_storage_url = urlparse(self.object_storage_public_endpoint_url)
            if public_storage_url.scheme != "https" or not public_storage_url.netloc:
                errors.append("OBJECT_STORAGE_PUBLIC_ENDPOINT_URL must be an absolute HTTPS URL")
        if self.rate_limit_requests < 1:
            errors.append("RATE_LIMIT_REQUESTS must be positive")
        if self.rate_limit_window_seconds < 1:
            errors.append("RATE_LIMIT_WINDOW_SECONDS must be positive")
        if self.agent_runtime_rate_limit_requests < 1:
            errors.append("AGENT_RUNTIME_RATE_LIMIT_REQUESTS must be positive")
        if self.agent_runtime_rate_limit_window_seconds < 1:
            errors.append(
                "AGENT_RUNTIME_RATE_LIMIT_WINDOW_SECONDS must be positive"
            )
        if self.metrics_require_service_key and not self.service_api_key:
            errors.append("SERVICE_API_KEY is required when METRICS_REQUIRE_SERVICE_KEY is true")
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
            if not self.auth_jwt_private_key_b64:
                errors.append("AUTH_JWT_PRIVATE_KEY_B64 is required in production")
            if not self.auth_jwt_issuer:
                errors.append("AUTH_JWT_ISSUER is required in production")
            if not self.auth_jwt_product_audience:
                errors.append("AUTH_JWT_PRODUCT_AUDIENCE is required in production")
            if not self.auth_jwt_runtime_audience:
                errors.append("AUTH_JWT_RUNTIME_AUDIENCE is required in production")
            if not self.service_api_key:
                errors.append("SERVICE_API_KEY is required in production for protected operational endpoints")
            if not self.agent_runtime_service_api_key:
                errors.append("AGENT_RUNTIME_SERVICE_API_KEY is required in production")
            if not self.agent_model_asset_public_base_url:
                errors.append(
                    "AGENT_MODEL_ASSET_PUBLIC_BASE_URL is required "
                    "in production"
                )
            if self.voice_provider == "local_stub":
                errors.append("VOICE_PROVIDER=local_stub cannot be used in production")
            if self.vision_provider == "local_stub":
                errors.append("VISION_PROVIDER=local_stub cannot be used in production")

        if errors:
            raise ValueError("; ".join(errors))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


@lru_cache(maxsize=8)
def load_auth_jwt_private_key(private_key_b64: str) -> rsa.RSAPrivateKey:
    try:
        private_key_pem = base64.b64decode(private_key_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("AUTH_JWT_PRIVATE_KEY_B64 must be valid base64") from exc
    if not private_key_pem.startswith(b"-----BEGIN PRIVATE KEY-----"):
        raise ValueError("AUTH_JWT_PRIVATE_KEY_B64 must contain an unencrypted PKCS#8 RSA private key")
    try:
        private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    except (TypeError, ValueError) as exc:
        raise ValueError("AUTH_JWT_PRIVATE_KEY_B64 must contain an unencrypted PKCS#8 RSA private key") from exc
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise ValueError("AUTH_JWT_PRIVATE_KEY_B64 must contain an unencrypted PKCS#8 RSA private key")
    if private_key.key_size < 2048:
        raise ValueError("AUTH_JWT_PRIVATE_KEY_B64 RSA key must be at least 2048 bits")
    return private_key


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


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        raise ValueError(f"{name} must be a number") from None


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
