from __future__ import annotations

from uuid import UUID

from fastapi import Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.dependencies import require_current_user
from ...api.surface import SurfaceAPIRouter, api_surface
from ...core.errors import ApiError, ErrorEnvelope
from ...infrastructure.db.session import get_session
from ..invites.repository import InviteCodeRepository
from ..users.models import AccountStatus, User
from .account_service import AuthAccountService, DeviceContext, IssuedTokenPair
from .account_lifecycle import AccountLifecycleService, ChallengeRejected
from .current_user import CurrentUser
from .email import QueuedAuthEmailSender
from .repository import AuthAccountRepository, AuthSessionRepository
from .schemas import AccountOperationResponse, AccountProfile, EmailChallengeRequest, EmailRegisterRequest, RegistrationCodeRequest, InviteLoginRequest, LoginRequest, LogoutResponse, PasswordResetConfirmRequest, PasswordChangeRequest, PasswordResetRequest, RefreshRequest, SignupRequest, TokenResponse, TokenUser
from .service import refresh_token_hash, AuthSessionService, RefreshTokenRevoked


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
        invite_code_repository=InviteCodeRepository(session),
    )


def get_account_lifecycle_service(request: Request, session: AsyncSession = Depends(get_session)) -> AccountLifecycleService:
    settings = request.app.state.settings
    return AccountLifecycleService(
        accounts=AuthAccountRepository(session),
        auth=AuthAccountService(account_repository=AuthAccountRepository(session), session_service=AuthSessionService(repository=AuthSessionRepository(session)), settings=settings),
        email_sender=QueuedAuthEmailSender(session, settings),
        token_key=settings.auth_email_token_key,
    )


@router.post("/register", response_model=AccountOperationResponse, status_code=202)
async def register(body: EmailRegisterRequest, service: AccountLifecycleService = Depends(get_account_lifecycle_service)) -> AccountOperationResponse:
    await service.start_registration(email=body.email)
    return AccountOperationResponse(status="verification_required")


@router.post("/verify-registration-code", response_model=AccountOperationResponse)
async def verify_registration_code(body: RegistrationCodeRequest, request: Request, response: Response,
                                   service: AccountLifecycleService = Depends(get_account_lifecycle_service)) -> AccountOperationResponse | JSONResponse:
    response.headers["Cache-Control"] = "private, no-store"
    try:
        await service.check_registration_code(email=body.email, token=body.token)
    except ChallengeRejected as error:
        return _challenge_response(error, request)
    return AccountOperationResponse(status="code_valid")


@router.post("/verify-email", response_model=TokenResponse)
async def verify_email(body: EmailChallengeRequest, request: Request, service: AccountLifecycleService = Depends(get_account_lifecycle_service)) -> TokenResponse | JSONResponse:
    if body.confirm_password != body.password:
        raise ApiError(code="validation_failed", message="Passwords do not match.", status=422)
    try:
        issued = await service.verify_email(email=body.email, token=body.token, password=body.password, device=_device_context(request=request, device_id=body.device_id))
    except ChallengeRejected as error:
        return _challenge_response(error, request)
    return _token_response(issued)


@router.post("/resend-verification", response_model=AccountOperationResponse, status_code=202)
async def resend_verification(body: PasswordResetRequest, service: AccountLifecycleService = Depends(get_account_lifecycle_service)) -> AccountOperationResponse:
    await service.resend_verification(email=body.email)
    return AccountOperationResponse(status="verification_if_required")


@router.post("/forgot-password", response_model=AccountOperationResponse, status_code=202)
async def forgot_password(body: PasswordResetRequest, service: AccountLifecycleService = Depends(get_account_lifecycle_service)) -> AccountOperationResponse:
    await service.request_password_reset(email=body.email)
    return AccountOperationResponse(status="reset_if_available")


@router.post("/reset-password", response_model=AccountOperationResponse)
async def reset_password(body: PasswordResetConfirmRequest, request: Request, service: AccountLifecycleService = Depends(get_account_lifecycle_service)) -> AccountOperationResponse | JSONResponse:
    if body.confirm_password != body.new_password:
        raise ApiError(code="validation_failed", message="Passwords do not match.", status=422)
    try:
        await service.reset_password(email=body.email, token=body.token, new_password=body.new_password)
    except ChallengeRejected as error:
        return _challenge_response(error, request)
    return AccountOperationResponse(status="password_reset")


@router.post("/change-password", response_model=AccountOperationResponse)
async def change_password(body: PasswordChangeRequest, current_user: CurrentUser = Depends(require_current_user),
                          service: AccountLifecycleService = Depends(get_account_lifecycle_service)) -> AccountOperationResponse:
    if body.confirm_password != body.new_password:
        raise ApiError(code="validation_failed", message="Passwords do not match.", status=422)
    await service.change_password(user_id=current_user.user_id, current_password=body.current_password,
                                  new_password=body.new_password)
    return AccountOperationResponse(status="password_changed")

@router.get("/me", response_model=AccountProfile)
async def account_me(current_user: CurrentUser = Depends(require_current_user), service: AccountLifecycleService = Depends(get_account_lifecycle_service)) -> AccountProfile:
    user = await service.accounts.get_user(user_id=current_user.user_id)
    if user is None:
        raise ApiError(code="authentication_required", message="Account is unavailable.", status=401)
    return AccountProfile(id=str(user.id), email=user.email, email_verified=user.email_verified_at is not None, account_status=AccountStatus(user.status),
        auth_providers=sorted({identity.provider for identity in user.identities}),
        created_at=user.created_at, updated_at=user.updated_at, last_login_at=user.last_login_at)


@router.delete("/me", response_model=AccountOperationResponse)
async def delete_account(current_user: CurrentUser = Depends(require_current_user), service: AccountLifecycleService = Depends(get_account_lifecycle_service)) -> AccountOperationResponse:
    user = await service.accounts.get_user(user_id=current_user.user_id)
    if user is None:
        raise ApiError(code="authentication_required", message="Account is unavailable.", status=401)
    await service.delete_account(user=user)
    return AccountOperationResponse(status="deletion_pending")


@router.post("/signup", response_model=AccountOperationResponse, status_code=202)
async def signup(body: SignupRequest, service: AccountLifecycleService = Depends(get_account_lifecycle_service)) -> AccountOperationResponse:
    await service.register(email=body.email, password=body.password)
    return AccountOperationResponse(status="verification_required")


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


@router.post("/invite-login", response_model=TokenResponse)
async def invite_login(
    body: InviteLoginRequest,
    request: Request,
    service: AuthAccountService = Depends(get_auth_account_service),
) -> TokenResponse:
    issued = await service.invite_login(
        invite_code=body.invite_code,
        device_context=_device_context(request=request, device_id=body.device_id),
    )
    return _token_response(issued)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    request: Request,
    service: AuthAccountService = Depends(get_auth_account_service),
) -> TokenResponse | JSONResponse:
    try:
        issued = await service.refresh(refresh_token=body.refresh_token)
    except RefreshTokenRevoked as error:
        # Returning normally lets get_session commit the family/session
        # revocations. Raising through that dependency would roll them back.
        envelope = ErrorEnvelope(code=error.code, message=error.message, status=error.status,
            request_id=getattr(request.state, "request_id", None))
        return JSONResponse(envelope.to_response_body(), status_code=error.status,
            headers={"Cache-Control": "private, no-store"})
    return _token_response(issued)


@router.post("/logout-session", response_model=LogoutResponse)
async def logout_session(body: RefreshRequest, service: AuthAccountService = Depends(get_auth_account_service)) -> LogoutResponse:
    repository = service.session_service.repository
    await repository.lock_refresh_user(refresh_token_hash(body.refresh_token))
    record = await repository.get_refresh_token_by_hash(token_hash=refresh_token_hash(body.refresh_token))
    if record is not None:
        await service.logout(session_id=record.session_id)
    return LogoutResponse()


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
    client_host = request.client.host if request.client else ""
    ip_address = client_host
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
        user=_user_response(issued.user),
    )


def _user_response(user: User) -> TokenUser:
    return TokenUser(id=str(user.id))


def _challenge_response(error: ChallengeRejected, request: Request) -> JSONResponse:
    # Normal dependency exit commits the failed-attempt counter; raising rolls it back.
    envelope = ErrorEnvelope(code=error.code, message=error.message, status=error.status,
                             request_id=getattr(request.state, "request_id", None))
    return JSONResponse(envelope.to_response_body(), status_code=error.status,
                        headers={"Cache-Control": "private, no-store"})
