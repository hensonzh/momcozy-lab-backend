from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse, Response

from ..core.errors import ApiError, ErrorEnvelope
from ..core.logging import log_unhandled_exception


ExceptionHandler = Callable[[Request, Exception], Response | Awaitable[Response]]


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, cast(ExceptionHandler, api_error_handler))
    app.add_exception_handler(StarletteHTTPException, cast(ExceptionHandler, http_error_handler))
    app.add_exception_handler(RequestValidationError, cast(ExceptionHandler, validation_error_handler))
    app.add_exception_handler(Exception, unhandled_error_handler)


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return _error_response(
        request,
        status=exc.status,
        code=exc.code,
        message=exc.message,
        details=exc.details,
    )


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _http_error_code(exc.status_code)
    message = _safe_http_message(exc)
    return _error_response(request, status=exc.status_code, code=code, message=message)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _error_response(
        request,
        status=422,
        code="validation_failed",
        message="Request validation failed.",
        details={"errors": _safe_validation_errors(exc.errors())},
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    log_unhandled_exception(
        request_id=str(getattr(request.state, "request_id", "") or ""),
        method=request.method,
        path=request.url.path,
        route=_route_path(request),
        exception_type=type(exc).__name__,
    )
    return _error_response(
        request,
        status=500,
        code="internal_error",
        message="Internal server error.",
    )


def _error_response(
    request: Request,
    *,
    status: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    request_id = str(getattr(request.state, "request_id", "") or "")
    envelope = ErrorEnvelope(
        code=code,
        message=message,
        status=status,
        request_id=request_id,
        details=details,
    )
    return JSONResponse(status_code=status, content=envelope.to_response_body(), headers={"X-Request-ID": request_id})


def _route_path(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", "")
    return str(path or request.url.path)


def _http_error_code(status_code: int) -> str:
    if status_code == 401:
        return "authentication_required"
    if status_code == 403:
        return "permission_denied"
    if status_code == 404:
        return "not_found"
    if status_code == 409:
        return "conflict"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "internal_error"
    return "http_error"


def _safe_http_message(exc: StarletteHTTPException) -> str:
    if exc.status_code >= 500:
        return "Internal server error."
    if isinstance(exc.detail, dict):
        value = exc.detail.get("message") or exc.detail.get("detail")
        if value:
            return str(value)
    if isinstance(exc.detail, str) and exc.detail:
        return exc.detail
    return "Request failed."


def _safe_validation_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    safe_errors: list[dict[str, Any]] = []
    for error in errors:
        if not isinstance(error, dict):
            continue
        safe_error: dict[str, Any] = {}
        for key in ("type", "loc", "msg"):
            if key in error:
                safe_error[key] = error[key]
        safe_errors.append(safe_error)
    return safe_errors
