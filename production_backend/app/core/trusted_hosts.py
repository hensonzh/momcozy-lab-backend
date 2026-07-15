from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .settings import Settings


def install_trusted_host_middleware(app: FastAPI, settings: Settings) -> None:
    if not settings.trusted_hosts:
        return

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.trusted_hosts))
