"""Configuration for the independent stateless stool analyzer."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SpecialtySettings:
    app_env: str = "production"
    market: str = ""
    service_shared_secret: str = field(default="", repr=False)
    openai_api_key: str = field(default="", repr=False)
    openai_model: str = "gpt-6-sol"
    request_timeout_seconds: float = 35.0

    @classmethod
    def from_env(cls) -> SpecialtySettings:
        return cls(
            app_env=os.getenv("APP_ENV", "production"),
            market=os.getenv("STOOL_SPECIALTY_MARKET", "").upper(),
            service_shared_secret=os.getenv("STOOL_SPECIALTY_SERVICE_SECRET", ""),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_model=os.getenv("STOOL_SPECIALTY_OPENAI_MODEL", "gpt-6-sol"),
            request_timeout_seconds=float(os.getenv("STOOL_SPECIALTY_REQUEST_TIMEOUT_SECONDS", "35")),
        )

    def validate(self) -> None:
        if self.app_env not in {"local", "test", "staging", "production"}:
            raise ValueError("Invalid APP_ENV")
        if self.market not in {"", "CN", "US"}:
            raise ValueError("STOOL_SPECIALTY_MARKET must be empty, CN or US")
        if self.service_shared_secret and len(self.service_shared_secret.encode("utf-8")) < 32:
            raise ValueError("STOOL_SPECIALTY_SERVICE_SECRET must be at least 32 bytes")
        if not math.isfinite(self.request_timeout_seconds) or self.request_timeout_seconds <= 0:
            raise ValueError("STOOL_SPECIALTY_REQUEST_TIMEOUT_SECONDS must be positive")

    @property
    def ready(self) -> bool:
        return (self.market in {"CN", "US"}
                and len(self.service_shared_secret.encode("utf-8")) >= 32
                and bool(self.openai_api_key and self.openai_model))
