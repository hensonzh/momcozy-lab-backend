from __future__ import annotations

from typing import Any
from uuid import UUID

import jwt as pyjwt
from jwt import ExpiredSignatureError, InvalidTokenError

from ...core.errors import ApiError
from ...core.settings import Settings
from .current_user import CurrentUser


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
