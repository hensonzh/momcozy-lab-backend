from __future__ import annotations

from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...core.errors import ApiError
from ...infrastructure.db.session import get_session
from .account_service import AuthAccountService, DeviceContext, IssuedTokenPair
from .current_user import CurrentUser
from .repository import AuthAccountRepository, AuthSessionRepository
from .schemas import LoginRequest, LogoutResponse, RefreshRequest, SignupRequest, TokenResponse, TokenUser
from .service import AuthSessionService


router = SurfaceAPIRouter(
    prefix="/auth",
    tags=["auth"],
    api_surface_metadata=api_surface("public_app_api", owner="auth", clients=["flutter"]),
)


def get_auth_account_service(request: Request, session: AsyncSession = Depends(get_session)) -> AuthAccountService:
    return AuthAccountService(
        account_repository=AuthAccountRepository(session),
        session_service=AuthSessionService(repository=AuthSessionRepository(session)),
        settings=request.app.state.settings,
    )


@router.post("/signup", response_model=TokenResponse, status_code=201)
async def signup(
    body: SignupRequest,
    request: Request,
    service: AuthAccountService = Depends(get_auth_account_service),
) -> TokenResponse:
    issued = await service.signup(
        email=body.email,
        password=body.password,
        display_name=body.display_name,
        device_context=_device_context(request=request, device_id=body.device_id),
    )
    return _token_response(issued)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    service: AuthAccountService = Depends(get_auth_account_service),
) -> TokenResponse:
    issued = await service.login(
        email=body.email,
        password=body.password,
        device_context=_device_context(request=request, device_id=body.device_id),
    )
    return _token_response(issued)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    service: AuthAccountService = Depends(get_auth_account_service),
) -> TokenResponse:
    issued = await service.refresh(refresh_token=body.refresh_token)
    return _token_response(issued)


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    current_user: CurrentUser = Depends(require_current_user),
    service: AuthAccountService = Depends(get_auth_account_service),
) -> LogoutResponse:
    try:
        session_id = UUID(current_user.session_id)
    except ValueError as exc:
        raise ApiError(code="authentication_required", message="Access token session is invalid.", status=401) from exc
    await service.logout(session_id=session_id)
    return LogoutResponse()


def _device_context(*, request: Request, device_id: str) -> DeviceContext:
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    client_host = request.client.host if request.client else ""
    ip_address = forwarded_for.split(",", 1)[0].strip() or client_host
    return DeviceContext(
        device_id=device_id,
        user_agent=request.headers.get("User-Agent", ""),
        ip_address=ip_address,
    )


def _token_response(issued: IssuedTokenPair) -> TokenResponse:
    return TokenResponse(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_in=issued.expires_in,
        user=TokenUser(id=str(issued.user.id), display_name=issued.user.display_name),
    )
