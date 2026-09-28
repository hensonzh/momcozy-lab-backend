"""Response projection must not lazy-load updated timestamps after async flush."""

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.infrastructure.db.base import Base
from app.modules.invites.models import InviteCode
from app.modules.invites.repository import InviteCodeRepository
from app.modules.invites.schemas import InviteCodeRead
from app.modules.invites.service import InviteCodeService
from app.modules.users.models import User


def test_disabled_invite_can_be_serialized_without_async_lazy_io() -> None:
    async def run() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(
                    lambda sync: Base.metadata.create_all(
                        sync, tables=[User.__table__, InviteCode.__table__]
                    )
                )
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            async with sessions.begin() as session:
                service = InviteCodeService(repository=InviteCodeRepository(session))
                created = await service.create_invite_code(label="release smoke")
                disabled = await service.disable_invite_code(code=created.code)
                response = InviteCodeRead.model_validate(disabled)
                assert response.status == "disabled"
                assert response.updated_at is not None
        finally:
            await engine.dispose()

    asyncio.run(run())
