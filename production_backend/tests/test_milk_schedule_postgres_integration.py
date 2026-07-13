import asyncio
import os
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from production_backend.app.core.errors import ApiError
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
            concurrent_owner_task = await repository.create_task(
                owner_user_id=owner_user_id,
                plan_id=None,
                task_date=date(2026, 7, 14),
                task_time="10:00",
                title="刚新增的日程",
                description="",
                payload={"duration_minutes": 30},
            )
            first_concurrent_task = await repository.create_task(
                owner_user_id=owner_user_id,
                plan_id=plan.id,
                task_date=date(2026, 7, 15),
                task_time="08:00",
                title="并发调整一",
                description="",
                payload={"duration_minutes": 30},
            )
            second_concurrent_task = await repository.create_task(
                owner_user_id=owner_user_id,
                plan_id=plan.id,
                task_date=date(2026, 7, 16),
                task_time="08:00",
                title="并发调整二",
                description="",
                payload={"duration_minutes": 30},
            )
            other_task = await repository.create_task(
                owner_user_id=other_user_id,
                plan_id=None,
                task_date=date(2026, 7, 14),
                task_time="10:00",
                title="other-owner",
                description="",
                payload={"task_type": "pumping"},
            )

        async with sessions.begin() as session:
            service = PlansService(repository=PlansRepository(session))
            with pytest.raises(ApiError) as exc_info:
                await service.reschedule_milk_tasks(
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
            assert exc_info.value.code == "milk_schedule_conflict"

        async with sessions.begin() as session:
            concurrent = await session.get(PlanTask, concurrent_owner_task.id)
            assert concurrent is not None
            concurrent.status = "completed"

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
            assert persisted_other is not None and persisted_other.task_time == "10:00"

        first_session = sessions()
        second_session = sessions()
        second_apply = None
        try:
            await first_session.begin()
            await second_session.begin()
            first_service = PlansService(repository=PlansRepository(first_session))
            second_service = PlansService(repository=PlansRepository(second_session))
            await first_service.reschedule_milk_tasks(
                owner_user_id=owner_user_id,
                plan_id=plan.id,
                updates=[
                    {
                        "task_id": str(first_concurrent_task.id),
                        "expected_plan_id": str(plan.id),
                        "expected_task_date": "2026-07-15",
                        "expected_task_time": "08:00",
                        "new_task_date": "2026-07-17",
                        "new_task_time": "08:00",
                    }
                ],
            )
            second_apply = asyncio.create_task(
                second_service.reschedule_milk_tasks(
                    owner_user_id=owner_user_id,
                    plan_id=plan.id,
                    updates=[
                        {
                            "task_id": str(second_concurrent_task.id),
                            "expected_plan_id": str(plan.id),
                            "expected_task_date": "2026-07-16",
                            "expected_task_time": "08:00",
                            "new_task_date": "2026-07-17",
                            "new_task_time": "08:00",
                        }
                    ],
                )
            )
            await asyncio.sleep(0.1)
            assert not second_apply.done()
            await first_session.commit()
            with pytest.raises(ApiError) as concurrent_exc:
                await asyncio.wait_for(second_apply, timeout=2)
            assert concurrent_exc.value.code == "milk_schedule_conflict"
            await second_session.rollback()
        finally:
            if second_apply is not None and not second_apply.done():
                second_apply.cancel()
            if first_session.in_transaction():
                await first_session.rollback()
            if second_session.in_transaction():
                await second_session.rollback()
            await first_session.close()
            await second_session.close()
    finally:
        await engine.dispose()
        cleanup_engine = create_async_engine(DATABASE_URL)
        async with cleanup_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await cleanup_engine.dispose()
