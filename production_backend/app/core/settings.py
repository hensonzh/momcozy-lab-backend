from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    app_name: str = "MomCozy Production Backend"
    app_version: str = "0.1.0"
    app_env: str = "local"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            app_name=os.getenv("APP_NAME", cls.app_name).strip() or cls.app_name,
            app_version=os.getenv("APP_VERSION", cls.app_version).strip() or cls.app_version,
            app_env=os.getenv("APP_ENV", cls.app_env).strip() or cls.app_env,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()

