from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import perf_counter

from fastapi import FastAPI, Request, Response

from .logging import log_http_request
from .request_id import REQUEST_ID_HEADER, normalize_request_id


def install_http_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = normalize_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        started_at = perf_counter()
        response = await call_next(request)
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
        return response


def _route_path(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", "")
    return str(path or request.url.path)


install_request_id_middleware = install_http_middleware
