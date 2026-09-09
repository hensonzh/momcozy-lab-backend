from fastapi import Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...api.surface import SurfaceAPIRouter, api_surface
from ...infrastructure.db import get_session
from ..audit.repository import AuditRepository
from ..audit.service import AuditService
from .account_service import AuthAccountService, DeviceContext
from .router import get_auth_account_service
from .schemas import TokenResponse, TokenUser
from .workbench_repository import WorkbenchAuthRepository
from .workbench_schemas import WorkbenchChallengeRead, WorkbenchLoginWrite, WorkbenchVerifyWrite
from .workbench_service import WorkbenchAuthService

router = SurfaceAPIRouter(prefix="/ibclc/auth", tags=["workbench-auth"],
    api_surface_metadata=api_surface("public_app_api", owner="auth", clients=["ibclc"]))


def get_workbench_auth_service(request: Request, session: AsyncSession = Depends(get_session),
    accounts: AuthAccountService = Depends(get_auth_account_service)) -> WorkbenchAuthService:
    return WorkbenchAuthService(WorkbenchAuthRepository(session), accounts, accounts.session_service,
        AuditService(repository=AuditRepository(session)), request.app.state.settings)


@router.post("/login", response_model=WorkbenchChallengeRead)
async def begin_workbench_login(body: WorkbenchLoginWrite, request: Request, response: Response,
    service: WorkbenchAuthService = Depends(get_workbench_auth_service)) -> WorkbenchChallengeRead:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.begin(email=body.email, password=body.password.get_secret_value(),
        device=DeviceContext(device_id=body.device_id, user_agent=request.headers.get("User-Agent", ""),
            ip_address=request.client.host if request.client else ""), request_id=request.state.request_id)


@router.post("/verify", response_model=TokenResponse)
async def verify_workbench_login(body: WorkbenchVerifyWrite, request: Request, response: Response,
    service: WorkbenchAuthService = Depends(get_workbench_auth_service)) -> TokenResponse | JSONResponse:
    result = await service.verify(challenge_token=body.challenge.get_secret_value(), code=body.code.get_secret_value(), request_id=request.state.request_id)
    if result.error is not None:
        return JSONResponse(status_code=result.error.status, content=result.error.to_response_body(), headers={"Cache-Control": "private, no-store"})
    issued = result.issued
    assert issued is not None
    response.headers["Cache-Control"] = "private, no-store"
    return TokenResponse(access_token=issued.access_token, refresh_token=issued.refresh_token, expires_in=issued.expires_in, user=TokenUser(id=str(issued.user.id)))
