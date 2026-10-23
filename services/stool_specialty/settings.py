"""Configuration for the independent stateless stool analyzer."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SpecialtySettings:
    app_env: str = "production"
    market: str = ""
    service_shared_secret: str = field(default="", repr=False)
    analysis_enabled: bool = False
    provider: str = "disabled"
    openai_api_key: str = field(default="", repr=False)
    openai_model: str = "gpt-5.4-mini"
    request_timeout_seconds: float = 35.0

    @classmethod
    def from_env(cls) -> SpecialtySettings:
        return cls(
            app_env=os.getenv("APP_ENV", "production"),
            market=os.getenv("STOOL_SPECIALTY_MARKET", "").upper(),
            service_shared_secret=os.getenv("STOOL_SPECIALTY_SERVICE_SECRET", ""),
            analysis_enabled=_bool("STOOL_SPECIALTY_ANALYSIS_ENABLED", False),
            provider=os.getenv("STOOL_SPECIALTY_PROVIDER", "disabled").lower(),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_model=os.getenv("STOOL_SPECIALTY_OPENAI_MODEL", "gpt-5.4-mini"),
            request_timeout_seconds=float(os.getenv("STOOL_SPECIALTY_REQUEST_TIMEOUT_SECONDS", "35")),
        )

    def validate(self) -> None:
        if self.app_env not in {"local", "test", "staging", "production"}:
            raise ValueError("Invalid APP_ENV")
        if self.market not in {"", "CN", "US"}:
            raise ValueError("STOOL_SPECIALTY_MARKET must be empty, CN or US")
        if self.service_shared_secret and len(self.service_shared_secret.encode("utf-8")) < 32:
            raise ValueError("STOOL_SPECIALTY_SERVICE_SECRET must be at least 32 bytes")
        if self.provider not in {"disabled", "openai"}:
            raise ValueError("STOOL_SPECIALTY_PROVIDER must be disabled or openai")
        if not math.isfinite(self.request_timeout_seconds) or self.request_timeout_seconds <= 0:
            raise ValueError("STOOL_SPECIALTY_REQUEST_TIMEOUT_SECONDS must be positive")
        if self.analysis_enabled and (self.market not in {"CN", "US"}
                or len(self.service_shared_secret.encode("utf-8")) < 32):
            raise ValueError("Enabled stool analysis requires market and service secret")
        if self.analysis_enabled and self.provider != "openai":
            raise ValueError("Enabled stool analysis requires STOOL_SPECIALTY_PROVIDER=openai")
        if self.analysis_enabled and (not self.openai_api_key or not self.openai_model):
            raise ValueError("Enabled stool analysis requires OPENAI_API_KEY and a model")
