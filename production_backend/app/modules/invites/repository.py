from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import InviteCode


class InviteCodeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_code(self, *, code: str, for_update: bool = False) -> InviteCode | None:
        statement = select(InviteCode).where(InviteCode.code == code)
        if for_update:
            statement = statement.with_for_update()
        return cast(InviteCode | None, await self.session.scalar(statement))

    async def list_recent(self, *, limit: int = 50) -> list[InviteCode]:
        statement = select(InviteCode).order_by(InviteCode.created_at.desc(), InviteCode.id.desc()).limit(limit)
        return list((await self.session.scalars(statement)).all())

    async def create(
        self,
        *,
        code: str,
        label: str,
        assigned_to: str,
        expires_at: datetime | None,
        created_by_service: str,
    ) -> InviteCode:
        invite_code = InviteCode(
            code=code,
            label=label,
            assigned_to=assigned_to,
            expires_at=expires_at,
            created_by_service=created_by_service,
        )
        self.session.add(invite_code)
        await self.session.flush()
        return invite_code

    async def bind(
        self,
        *,
        invite_code: InviteCode,
        device_id: str,
        user_id: UUID,
    ) -> InviteCode:
        invite_code.bound_device_id = device_id
        invite_code.bound_user_id = user_id
        invite_code.used_count = max(invite_code.used_count or 0, 1)
        await self.session.flush()
        return invite_code

    async def disable(self, *, invite_code: InviteCode, disabled_at: datetime) -> InviteCode:
        invite_code.status = "disabled"
        invite_code.disabled_at = disabled_at
        await self.session.flush()
        return invite_code
