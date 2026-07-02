from __future__ import annotations

from fastapi import FastAPI

from .api.v1.router import router as v1_router
from .core.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
    )
    app.include_router(v1_router)
    return app

