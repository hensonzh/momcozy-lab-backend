from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import jwt as pyjwt
from jwt import ExpiredSignatureError, InvalidTokenError

from ...core.errors import ApiError
from ...core.settings import Settings
from .current_user import CurrentUser


ACCESS_TOKEN_TTL = timedelta(minutes=15)


def authenticate_access_token(token: str, settings: Settings) -> CurrentUser:
    if not settings.auth_jwt_secret:
        raise ApiError(
            code="auth_not_configured",
            message="Authentication is not configured.",
            status=500,
        )

    try:
        payload = pyjwt.decode(
            token,
            settings.auth_jwt_secret,
            algorithms=[settings.auth_jwt_algorithm],
            issuer=settings.auth_jwt_issuer or None,
            audience=settings.auth_jwt_audience or None,
            options={"require": ["sub", "exp"], "verify_aud": bool(settings.auth_jwt_audience)},
        )
    except ExpiredSignatureError as exc:
        raise ApiError(code="authentication_required", message="Access token expired.", status=401) from exc
    except InvalidTokenError as exc:
        raise ApiError(code="authentication_required", message="Access token is invalid.", status=401) from exc

    subject = str(payload.get("sub") or "").strip()
    try:
        user_id = UUID(subject)
    except ValueError as exc:
        raise ApiError(code="authentication_required", message="Access token subject is invalid.", status=401) from exc

    return CurrentUser(
        user_id=user_id,
        subject=subject,
        session_id=str(payload.get("sid") or ""),
        token_id=str(payload.get("jti") or ""),
        roles=frozenset(_claim_set(payload.get("roles"))),
        permissions=frozenset(_permissions(payload)),
    )


def issue_access_token(
    *,
    user_id: UUID,
    session_id: UUID,
    settings: Settings,
    roles: frozenset[str] = frozenset(),
    permissions: frozenset[str] = frozenset(),
    clock: Callable[[], datetime] | None = None,
) -> tuple[str, int]:
    if not settings.auth_jwt_secret:
        raise ApiError(
            code="auth_not_configured",
            message="Authentication is not configured.",
            status=500,
        )

    now = (clock or _utcnow)()
    expires_at = now + ACCESS_TOKEN_TTL
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "sid": str(session_id),
        "jti": f"at_{uuid4().hex}",
        "roles": sorted(roles),
        "permissions": sorted(permissions),
        "iat": now,
        "exp": expires_at,
    }
    if settings.auth_jwt_issuer:
        payload["iss"] = settings.auth_jwt_issuer
    if settings.auth_jwt_audience:
        payload["aud"] = settings.auth_jwt_audience

    token = pyjwt.encode(payload, settings.auth_jwt_secret, algorithm=settings.auth_jwt_algorithm)
    return token, int(ACCESS_TOKEN_TTL.total_seconds())


def _permissions(payload: dict[str, Any]) -> set[str]:
    values = set(_claim_set(payload.get("permissions")))
    scope = payload.get("scope")
    if isinstance(scope, str):
        values.update(item for item in scope.split(" ") if item)
    return values


def _claim_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value} if value else set()
    if isinstance(value, list | tuple | set):
        return {str(item) for item in value if str(item)}
    return {str(value)}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
