from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import cast

from fastapi import FastAPI, Request, Response
from starlette.responses import JSONResponse

from .errors import ErrorEnvelope
from .logging import log_http_request
from .rate_limit import AUTH_ENTRY_PATHS, RATE_LIMIT_EXEMPT_PATHS, WORKBENCH_LOGIN_PATHS, RateLimitDecision, check_rate_limit
from .request_id import REQUEST_ID_HEADER, normalize_request_id
from .settings import Settings


def install_http_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = normalize_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        started_at = perf_counter()
        settings = cast(Settings, request.app.state.settings)
        rate_limit_decision = await check_rate_limit(request, settings)
        if rate_limit_decision.allowed:
            response = await call_next(request)
        else:
            response = _rate_limited_response(request_id=request_id, decision=rate_limit_decision)
        duration_ms = (perf_counter() - started_at) * 1000
        route = _route_path(request)
        request.app.state.request_metrics.record(
            method=request.method,
            route=route,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        log_http_request(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            route=route,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        _apply_security_headers(response=response, settings=settings)
        if request.url.path.startswith(("/v1/auth/", "/v1/notifications")):
            response.headers["Cache-Control"] = "private, no-store"
        if (settings.rate_limit_enabled or request.url.path in WORKBENCH_LOGIN_PATHS | AUTH_ENTRY_PATHS) and request.url.path not in RATE_LIMIT_EXEMPT_PATHS:
            response.headers["X-RateLimit-Limit"] = str(rate_limit_decision.limit)
            response.headers["X-RateLimit-Remaining"] = str(rate_limit_decision.remaining)
        return response


def _route_path(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", "")
    return str(path or request.url.path)


def _rate_limited_response(*, request_id: str, decision: RateLimitDecision) -> JSONResponse:
    envelope = ErrorEnvelope(
        code="rate_limited",
        message="Too many requests.",
        status=429,
        request_id=request_id,
        details={"retry_after_seconds": decision.retry_after_seconds},
    )
    return JSONResponse(
        status_code=429,
        content=envelope.to_response_body(),
        headers={
            "Retry-After": str(decision.retry_after_seconds),
        },
    )


def _apply_security_headers(*, response: Response, settings: Settings) -> None:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if settings.is_deployed:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")


install_request_id_middleware = install_http_middleware
