"""Independent, stateless stool-analysis stream."""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import UUID

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .analyzer import OpenAIStoolAnalyzer, StoolAnalyzer
from .contracts import AnalysisEvent, AnalysisInput, build_success_events
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


def create_app(settings: SpecialtySettings, *, analyzer: StoolAnalyzer | None = None) -> FastAPI:
    settings.validate()
    if analyzer is None and settings.analysis_enabled:
        analyzer = OpenAIStoolAnalyzer(
            api_key=settings.openai_api_key, model=settings.openai_model,
            timeout_seconds=settings.request_timeout_seconds,
        )
    service = FastAPI(title="Stool Specialty Service", version="1.0.0")
    service.state.settings = settings
    service.state.analyzer = analyzer

    @service.middleware("http")
    async def closed_gate_and_no_store(
        request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response: Response
        if request.url.path == "/v1/analyze/stream" and not settings.analysis_enabled:
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
        return {"status": "ready" if settings.analysis_enabled else "closed"}

    @service.post("/v1/analyze/stream", responses={503: {"description": "Analysis unavailable"}})
    async def analyze_stream(
        payload: AnalysisInput, claims: dict[str, Any] = Depends(_identity),
    ) -> StreamingResponse:
        if (claims["purpose"] != "stool.analyze" or claims["analysis_id"] != str(payload.analysis_id)
                or claims["market"] != payload.market):
            raise _deny(403, "service_scope_mismatch")
        resolved_analyzer: StoolAnalyzer | None = service.state.analyzer
        if resolved_analyzer is None:
            raise _deny(503, "specialty_analysis_unavailable")

        async def events() -> AsyncIterator[str]:
            started = AnalysisEvent(
                analysis_id=payload.analysis_id, sequence=2,
                event_type="stool.analysis.started",
            )
            yield _sse(started)
            try:
                result = await resolved_analyzer.analyze(payload)
                success = build_success_events(payload.analysis_id, result)
                for event in success[1:]:
                    yield _sse(event)
            except Exception:
                # Never return provider bodies, prompts, image bytes or exception text.
                yield _sse(AnalysisEvent(
                    analysis_id=payload.analysis_id, sequence=3,
                    event_type="stool.analysis.failed", payload={"code": "analysis_failed"},
                ))

        return StreamingResponse(events(), media_type="text/event-stream", headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        })

    return service


def _sse(event: AnalysisEvent) -> str:
    return f"id: {event.sequence}\nevent: {event.event_type}\ndata: {event.model_dump_json()}\n\n"
