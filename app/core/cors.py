from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .request_id import REQUEST_ID_HEADER
from .settings import Settings


def install_cors_middleware(app: FastAPI, settings: Settings) -> None:
    if not settings.cors_allowed_origins:
        return

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allowed_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            REQUEST_ID_HEADER,
            "X-Service-Key",
        ],
        expose_headers=[
            REQUEST_ID_HEADER,
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "Retry-After",
        ],
    )
