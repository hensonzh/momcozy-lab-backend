"""Independent, stateless analysis stream. No original storage, database or clinical output."""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import UUID

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .contracts import AnalysisEvent, AnalysisInput
from .settings import SpecialtySettings

AUDIENCE = "momcozy-stool-specialty-v1"
ISSUER = "momcozy-product-backend"
_BEARER = HTTPBearer(auto_error=False)


def _deny(status: int, code: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code})


def _identity(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(_BEARER),
) -> dict[str, Any]:
    settings: SpecialtySettings = request.app.state.settings
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _deny(401, "service_auth_required")
    try:
        claims = jwt.decode(
            credentials.credentials, settings.service_shared_secret,
            algorithms=["HS256"], audience=AUDIENCE, issuer=ISSUER,
            options={"require": ["iss", "aud", "sub", "iat", "exp", "jti", "purpose", "market", "analysis_id"]},
        )
        if (claims["sub"] != "product-backend" or not isinstance(claims["iat"], int)
                or not isinstance(claims["exp"], int) or claims["exp"] - claims["iat"] > 60
                or claims["exp"] <= claims["iat"] or not isinstance(claims["jti"], str)):
            raise ValueError("Invalid service claims")
        UUID(claims["analysis_id"])
    except (jwt.PyJWTError, ValueError, KeyError, TypeError) as exc:
        raise _deny(401, "service_auth_invalid") from exc
    if claims["market"] != settings.market:
        raise _deny(403, "market_scope_mismatch")
    return claims


def create_app(settings: SpecialtySettings) -> FastAPI:
    settings.validate()
    service = FastAPI(title="Stool Specialty Service", version="0.2.0")
    service.state.settings = settings

    @service.middleware("http")
    async def closed_gate_and_no_store(
        request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response: Response
        if request.url.path == "/v1/analyze/stream" and not settings.synthetic_analyze_enabled:
            response = JSONResponse(status_code=503, content={"detail": {"code": "specialty_analysis_unavailable"}})
        else:
            response = await call_next(request)
        if request.url.path == "/v1/analyze/stream":
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @service.exception_handler(RequestValidationError)
    async def invalid_input(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default detail can echo a rejected base64 photo.
        return JSONResponse(status_code=422, content={"detail": {"code": "specialty_input_invalid"}})

    @service.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ready"}

    @service.post("/v1/analyze/stream", responses={503: {"description": "Analysis unavailable"}})
    async def analyze_stream(
        payload: AnalysisInput, claims: dict[str, Any] = Depends(_identity),
    ) -> StreamingResponse:
        if (claims["purpose"] != "stool.analyze" or claims["analysis_id"] != str(payload.analysis_id)
                or claims["market"] != payload.market):
            raise _deny(403, "service_scope_mismatch")

        async def events() -> AsyncIterator[str]:
            # Clinical fields/results are not enabled. The Product backend owns
            # the first card event and all durable replay; we emit only terminal failure.
            for event in (
                AnalysisEvent(analysis_id=payload.analysis_id, sequence=2, event_type="stool.analysis.started"),
                AnalysisEvent(analysis_id=payload.analysis_id, sequence=3, event_type="stool.analysis.failed"),
            ):
                yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {event.model_dump_json()}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream", headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        })

    return service
