from __future__ import annotations

import json
import logging
from typing import Any

from .settings import Settings


HTTP_LOGGER_NAME = "production_backend.http"
ERROR_LOGGER_NAME = "production_backend.errors"


def configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(message)s",
        force=False,
    )


def log_http_request(
    *,
    request_id: str,
    method: str,
    path: str,
    route: str,
    status_code: int,
    duration_ms: float,
) -> None:
    logging.getLogger(HTTP_LOGGER_NAME).info(
        _json_line(
            {
                "event": "http.request",
                "request_id": request_id,
                "method": method,
                "path": path,
                "route": route,
                "status_code": status_code,
                "duration_ms": round(duration_ms, 3),
            }
        )
    )


def log_unhandled_exception(
    *,
    request_id: str,
    method: str,
    path: str,
    route: str,
    exception_type: str,
) -> None:
    logging.getLogger(ERROR_LOGGER_NAME).error(
        _json_line(
            {
                "event": "http.unhandled_exception",
                "request_id": request_id,
                "method": method,
                "path": path,
                "route": route,
                "exception_type": exception_type,
            }
        )
    )


def _json_line(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)
