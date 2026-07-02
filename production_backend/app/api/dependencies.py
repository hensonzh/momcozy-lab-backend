from __future__ import annotations

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..core.errors import ApiError
from ..infrastructure.object_storage import ObjectStorage
from ..modules.auth import CurrentUser, ServiceClient, authenticate_access_token, authenticate_service_key


bearer_scheme = HTTPBearer(auto_error=False)


async def require_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ApiError(code="authentication_required", message="Bearer access token is required.", status=401)

    return authenticate_access_token(credentials.credentials, request.app.state.settings)


def get_object_storage(request: Request) -> ObjectStorage:
    return request.app.state.object_storage


def normalize_idempotency_key(value: str | None) -> str | None:
    key = str(value or "").strip()
    if not key:
        return None
    if len(key) > 255:
        raise ApiError(code="validation_failed", message="Idempotency-Key is too long.", status=422)
    return key


async def optional_idempotency_key(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str | None:
    return normalize_idempotency_key(idempotency_key)


async def require_service_client(
    request: Request,
    service_key: str | None = Header(default=None, alias="X-Service-Key"),
) -> ServiceClient:
    if not service_key:
        raise ApiError(code="authentication_required", message="X-Service-Key is required.", status=401)
    return authenticate_service_key(service_key, request.app.state.settings)
