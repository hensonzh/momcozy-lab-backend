"""The stateless analyzer is closed until the clinical and privacy gates pass."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SpecialtySettings:
    app_env: str = "production"
    market: str = ""
    service_shared_secret: str = field(default="", repr=False)
    synthetic_analyze_enabled: bool = False

    @classmethod
    def from_env(cls) -> SpecialtySettings:
        return cls(
            app_env=os.getenv("APP_ENV", "production"),
            market=os.getenv("STOOL_SPECIALTY_MARKET", "").upper(),
            service_shared_secret=os.getenv("STOOL_SPECIALTY_SERVICE_SECRET", ""),
            synthetic_analyze_enabled=os.getenv("STOOL_SPECIALTY_SYNTHETIC_ANALYZE_ENABLED", "false").lower() == "true",
        )

    def validate(self) -> None:
        if self.app_env not in {"local", "test", "staging", "production"}:
            raise ValueError("Invalid APP_ENV")
        if self.market not in {"CN", "US"}:
            raise ValueError("STOOL_SPECIALTY_MARKET must be CN or US")
        if len(self.service_shared_secret.encode("utf-8")) < 32:
            raise ValueError("STOOL_SPECIALTY_SERVICE_SECRET must be at least 32 bytes")
        if self.synthetic_analyze_enabled and self.app_env != "test":
            raise ValueError("Synthetic analysis is permitted only in tests")
