from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..users.models import User
from .models import CareEligibility, CareEpisode, CareOrder, CareProvider


class CareRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def providers(self, *, sandbox: bool) -> list[CareProvider]:
        return list(await self.session.scalars(select(CareProvider).where(
            CareProvider.active.is_(True), CareProvider.sandbox.is_(sandbox)).order_by(CareProvider.display_name)))

    async def lock_owner(self, owner: UUID) -> None:
        await self.session.scalar(select(User.id).where(User.id == owner).with_for_update())

    async def eligibility(self, owner: UUID, record_id: UUID) -> CareEligibility | None:
        return cast(CareEligibility | None, await self.session.scalar(select(CareEligibility).where(
            CareEligibility.id == record_id, CareEligibility.owner_user_id == owner)))

    async def order(self, owner: UUID, order_id: UUID, *, lock: bool = False) -> CareOrder | None:
        statement = select(CareOrder).where(CareOrder.id == order_id, CareOrder.owner_user_id == owner)
        if lock:
            statement = statement.with_for_update()
        return cast(CareOrder | None, await self.session.scalar(statement.execution_options(populate_existing=True)))

    async def existing_order(self, owner: UUID, package_id: str, *, payment_mode: str = "sandbox") -> CareOrder | None:
        return cast(CareOrder | None, await self.session.scalar(select(CareOrder).outerjoin(CareEpisode, CareEpisode.order_id == CareOrder.id).where(
            CareOrder.owner_user_id == owner, CareOrder.package_id == package_id, CareOrder.payment_mode == payment_mode,
            or_(CareOrder.status.in_(["pending", "processing", "requires_action", "reconciling"]),
                and_(CareOrder.status == "paid", CareEpisode.status.in_(["active", "paused", "provisioning_pending"])))).order_by(CareOrder.created_at.desc()).limit(1)))

    async def orders(self, owner: UUID) -> list[CareOrder]:
        return list(await self.session.scalars(select(CareOrder).where(CareOrder.owner_user_id == owner).order_by(CareOrder.created_at.desc())))

    async def episodes(self, owner: UUID) -> list[CareEpisode]:
        return list(await self.session.scalars(select(CareEpisode).where(CareEpisode.owner_user_id == owner).order_by(CareEpisode.created_at.desc())))

    async def episode_for_order(self, owner: UUID, order_id: UUID) -> CareEpisode | None:
        return cast(CareEpisode | None, await self.session.scalar(select(CareEpisode).where(
            CareEpisode.owner_user_id == owner, CareEpisode.order_id == order_id)))

    async def add(self, record: Any) -> None:
        self.session.add(record)
        await self.session.flush()

    async def flush(self) -> None:
        await self.session.flush()
