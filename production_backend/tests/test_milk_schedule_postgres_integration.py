import asyncio
import os
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from production_backend.app.infrastructure.db.base import Base
from production_backend.app.modules.plans.models import Plan, PlanTask
from production_backend.app.modules.plans.repository import PlansRepository
from production_backend.app.modules.plans.service import PlansService
from production_backend.app.modules.users.models import User


DATABASE_URL = os.getenv("MOMCOZY_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="Set MOMCOZY_TEST_DATABASE_URL to an isolated PostgreSQL database to run repository integration coverage.",
)


def test_postgres_reschedule_persists_batch_and_keeps_other_owner_untouched() -> None:
    asyncio.run(_postgres_scenario())


async def _postgres_scenario() -> None:
    schema = f"milk_parity_{uuid4().hex}"
    admin_engine = create_async_engine(DATABASE_URL)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    await admin_engine.dispose()

    engine = create_async_engine(
        DATABASE_URL,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: Base.metadata.create_all(
                    sync_connection,
                    tables=[User.__table__, Plan.__table__, PlanTask.__table__],
                )
            )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner_user_id = uuid4()
        other_user_id = uuid4()
        async with sessions.begin() as session:
            session.add_all(
                [
                    User(id=owner_user_id, display_name="owner"),
                    User(id=other_user_id, display_name="other"),
                ]
            )
            await session.flush()
            repository = PlansRepository(session)
            plan = await repository.create_plan(
                owner_user_id=owner_user_id,
                plan_type="milk_management",
                title="稳奶计划",
                summary="",
                source="agent_action",
                payload={"start_date": "2026-07-14", "days": 1},
            )
            owner_task = await repository.create_task(
                owner_user_id=owner_user_id,
                plan_id=plan.id,
                task_date=date(2026, 7, 14),
                task_time="11:00",
                title="吸奶",
                description="",
                payload={"task_type": "pumping"},
            )
            other_task = await repository.create_task(
                owner_user_id=other_user_id,
                plan_id=None,
                task_date=date(2026, 7, 14),
                task_time="11:00",
                title="other-owner",
                description="",
                payload={"task_type": "pumping"},
            )

        async with sessions.begin() as session:
            service = PlansService(repository=PlansRepository(session))
            applied = await service.reschedule_milk_tasks(
                owner_user_id=owner_user_id,
                plan_id=plan.id,
                updates=[
                    {
                        "task_id": str(owner_task.id),
                        "expected_plan_id": str(plan.id),
                        "expected_task_date": "2026-07-14",
                        "expected_task_time": "11:00",
                        "new_task_date": "2026-07-14",
                        "new_task_time": "10:00",
                    }
                ],
            )
            assert applied[0].task_time == "10:00"

        async with sessions() as session:
            persisted_owner = await session.scalar(select(PlanTask).where(PlanTask.id == owner_task.id))
            persisted_other = await session.scalar(select(PlanTask).where(PlanTask.id == other_task.id))
            assert persisted_owner is not None and persisted_owner.task_time == "10:00"
            assert persisted_other is not None and persisted_other.task_time == "11:00"
    finally:
        await engine.dispose()
        cleanup_engine = create_async_engine(DATABASE_URL)
        async with cleanup_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await cleanup_engine.dispose()
