from __future__ import annotations

from collections.abc import Callable
from typing import AsyncContextManager, cast
from uuid import UUID

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import ApiError
from ..core.settings import Settings
from ..infrastructure.object_storage import ObjectStorage
from ..modules.auth import (
    CurrentUser,
    ServiceClient,
    authenticate_access_token,
    authenticate_agent_runtime_service_key,
    authenticate_service_key,
)
from ..modules.auth.repository import AuthSessionRepository


bearer_scheme = HTTPBearer(auto_error=False)


async def require_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ApiError(code="authentication_required", message="Bearer access token is required.", status=401)

    return await authenticate_request_user(
        token=credentials.credentials,
        settings=request.app.state.settings,
        session_factory=getattr(request.app.state, "db_session_factory", None),
    )


async def authenticate_request_user(
    *,
    token: str,
    settings: Settings,
    session_factory: Callable[[], AsyncContextManager[AsyncSession]] | None,
) -> CurrentUser:
    current_user = authenticate_access_token(token, settings)
    if settings.is_production or settings.auth_require_active_session:
        if session_factory is None:
            raise ApiError(code="auth_session_check_not_configured", message="Authentication session check is not configured.", status=500)
        await _require_active_device_session(current_user=current_user, session_factory=session_factory)
    return current_user


async def _require_active_device_session(
    *,
    current_user: CurrentUser,
    session_factory: Callable[[], AsyncContextManager[AsyncSession]],
) -> None:
    try:
        session_id = UUID(current_user.session_id)
    except ValueError as exc:
        raise ApiError(code="authentication_required", message="Access token session is invalid.", status=401) from exc

    async with session_factory() as session:
        device_session = await AuthSessionRepository(session).get_device_session(session_id=session_id)
    if device_session is None or device_session.status != "active" or device_session.user_id != current_user.user_id:
        raise ApiError(code="authentication_required", message="Session is no longer active.", status=401)


def get_object_storage(request: Request) -> ObjectStorage:
    return cast(ObjectStorage, request.app.state.object_storage)


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


async def require_agent_runtime_client(
    request: Request,
    service_key: str | None = Header(default=None, alias="X-Service-Key"),
) -> ServiceClient:
    if not service_key:
        raise ApiError(code="authentication_required", message="X-Service-Key is required.", status=401)
    return authenticate_agent_runtime_service_key(service_key, request.app.state.settings)
