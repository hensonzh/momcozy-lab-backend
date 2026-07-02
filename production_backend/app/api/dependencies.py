from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..core.errors import ApiError
from ..modules.auth import CurrentUser, authenticate_access_token


bearer_scheme = HTTPBearer(auto_error=False)


async def require_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ApiError(code="authentication_required", message="Bearer access token is required.", status=401)

    return authenticate_access_token(credentials.credentials, request.app.state.settings)
