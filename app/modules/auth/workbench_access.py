from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ApiError
from ...core.settings import Settings
from ..care.models import CareProvider
from ..users.models import User
from .models import DeviceSession


async def workbench_session_roles(device: DeviceSession, session: AsyncSession, settings: Settings) -> frozenset[str]:
    if device.mfa_verified_at is None or device.mfa_verified_at + timedelta(hours=settings.ibclc_session_hours) <= datetime.now(timezone.utc):
        raise ApiError(code="mfa_required", message="Sign in again to verify this workbench session.", status=401)
    active = await session.scalar(select(CareProvider.user_id).join(User, User.id == CareProvider.user_id).where(
        CareProvider.user_id == device.user_id, CareProvider.active.is_(True), User.status == "active"))
    if active is None:
        raise ApiError(code="permission_denied", message="Workbench access is not active.", status=403)
    return frozenset({"ibclc"})
