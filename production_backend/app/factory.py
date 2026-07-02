from __future__ import annotations

from fastapi import FastAPI

from .api.error_handlers import install_error_handlers
from .api.v1.router import router as v1_router
from .core.lifespan import build_lifespan
from .core.logging import configure_logging
from .core.metrics import RequestMetrics
from .core.middleware import install_http_middleware
from .core.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_settings.validate_for_startup()
    configure_logging(resolved_settings)
    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        lifespan=build_lifespan(resolved_settings),
    )
    app.state.settings = resolved_settings
    app.state.request_metrics = RequestMetrics()
    install_http_middleware(app)
    install_error_handlers(app)
    app.include_router(v1_router)
    return app
