from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..care.models import CareProvider
from .workbench_models import WorkbenchLoginChallenge, WorkbenchMfaCredential


class WorkbenchAuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def provider(self, provider_id: UUID) -> CareProvider | None:
        return cast(CareProvider | None, await self.session.scalar(select(CareProvider).where(CareProvider.user_id == provider_id)
            .with_for_update(read=True).execution_options(populate_existing=True)))

    async def credential(self, provider_id: UUID) -> WorkbenchMfaCredential | None:
        return cast(WorkbenchMfaCredential | None, await self.session.scalar(select(WorkbenchMfaCredential)
            .where(WorkbenchMfaCredential.provider_id == provider_id).with_for_update().execution_options(populate_existing=True)))

    async def challenge(self, token_hash: str) -> WorkbenchLoginChallenge | None:
        return cast(WorkbenchLoginChallenge | None, await self.session.scalar(select(WorkbenchLoginChallenge)
            .where(WorkbenchLoginChallenge.token_hash == token_hash).execution_options(populate_existing=True)))

    async def recent_challenges(self, provider_id: UUID, after: datetime) -> int:
        return int(await self.session.scalar(select(func.count()).select_from(WorkbenchLoginChallenge).where(
            WorkbenchLoginChallenge.provider_id == provider_id, WorkbenchLoginChallenge.created_at >= after)) or 0)

    async def add(self, value: WorkbenchLoginChallenge) -> None:
        self.session.add(value)
        await self.session.flush()

    async def flush(self) -> None:
        await self.session.flush()
