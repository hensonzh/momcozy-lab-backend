import asyncio
import os
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.errors import ApiError
from app.infrastructure.db.base import Base
from app.modules.plans.models import Plan, PlanTask
from app.modules.plans.repository import PlansRepository
from app.modules.plans.service import PlansService
from app.modules.users.models import User


DATABASE_URL = os.getenv("MOMCOZY_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="Set MOMCOZY_TEST_DATABASE_URL to an isolated PostgreSQL database to run repository integration coverage.",
)


def test_postgres_reschedule_persists_batch_and_keeps_other_owner_untouched() -> None:
    asyncio.run(_postgres_scenario())


def test_postgres_task_writes_share_owner_schedule_lock_without_cross_owner_blocking() -> None:
    asyncio.run(_postgres_task_write_lock_scenario())


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


async def _postgres_task_write_lock_scenario() -> None:
    schema = f"milk_task_lock_{uuid4().hex}"
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
                payload={},
            )
            create_conflict_task = await repository.create_task(
                owner_user_id=owner_user_id,
                plan_id=plan.id,
                task_date=date(2026, 7, 20),
                task_time="11:00",
                title="创建并发候选",
                description="",
                payload={"duration_minutes": 30},
            )
            update_conflict_task = await repository.create_task(
                owner_user_id=owner_user_id,
                plan_id=plan.id,
                task_date=date(2026, 7, 21),
                task_time="11:00",
                title="更新并发候选",
                description="",
                payload={"duration_minutes": 30},
            )
            fixed_task = await repository.create_task(
                owner_user_id=owner_user_id,
                plan_id=None,
                task_date=date(2026, 7, 21),
                task_time="09:00",
                title="待移出任务",
                description="",
                payload={"duration_minutes": 30},
            )

        create_session = sessions()
        reschedule_session = sessions()
        blocked_reschedule = None
        try:
            await create_session.begin()
            await reschedule_session.begin()
            created = await PlansService(repository=PlansRepository(create_session)).create_task(
                owner_user_id=owner_user_id,
                task_date=date(2026, 7, 20),
                task_time="10:00",
                title="刚创建的日程",
            )
            assert created.task_time == "10:00"
            blocked_reschedule = asyncio.create_task(
                PlansService(repository=PlansRepository(reschedule_session)).reschedule_milk_tasks(
                    owner_user_id=owner_user_id,
                    plan_id=plan.id,
                    updates=[
                        {
                            "task_id": str(create_conflict_task.id),
                            "expected_plan_id": str(plan.id),
                            "expected_task_date": "2026-07-20",
                            "expected_task_time": "11:00",
                            "new_task_date": "2026-07-20",
                            "new_task_time": "10:00",
                        }
                    ],
                )
            )
            await asyncio.sleep(0.1)
            assert not blocked_reschedule.done()
            await create_session.commit()
            with pytest.raises(ApiError) as create_conflict:
                await asyncio.wait_for(blocked_reschedule, timeout=2)
            assert create_conflict.value.code == "milk_schedule_conflict"
            await reschedule_session.rollback()
        finally:
            if blocked_reschedule is not None and not blocked_reschedule.done():
                blocked_reschedule.cancel()
            if create_session.in_transaction():
                await create_session.rollback()
            if reschedule_session.in_transaction():
                await reschedule_session.rollback()
            await create_session.close()
            await reschedule_session.close()

        update_session = sessions()
        old_date_reschedule_session = sessions()
        blocked_old_date_reschedule = None
        try:
            await update_session.begin()
            await old_date_reschedule_session.begin()
            moved = await PlansService(repository=PlansRepository(update_session)).update_task(
                owner_user_id=owner_user_id,
                task_id=fixed_task.id,
                updates={"task_date": date(2026, 7, 22), "task_time": "09:00"},
            )
            assert moved.task_date == date(2026, 7, 22)
            blocked_old_date_reschedule = asyncio.create_task(
                PlansService(repository=PlansRepository(old_date_reschedule_session)).reschedule_milk_tasks(
                    owner_user_id=owner_user_id,
                    plan_id=plan.id,
                    updates=[
                        {
                            "task_id": str(update_conflict_task.id),
                            "expected_plan_id": str(plan.id),
                            "expected_task_date": "2026-07-21",
                            "expected_task_time": "11:00",
                            "new_task_date": "2026-07-21",
                            "new_task_time": "09:00",
                        }
                    ],
                )
            )
            await asyncio.sleep(0.1)
            assert not blocked_old_date_reschedule.done()
            await update_session.commit()
            applied = await asyncio.wait_for(blocked_old_date_reschedule, timeout=2)
            assert applied[0].task_time == "09:00"
            await old_date_reschedule_session.commit()
        finally:
            if blocked_old_date_reschedule is not None and not blocked_old_date_reschedule.done():
                blocked_old_date_reschedule.cancel()
            if update_session.in_transaction():
                await update_session.rollback()
            if old_date_reschedule_session.in_transaction():
                await old_date_reschedule_session.rollback()
            await update_session.close()
            await old_date_reschedule_session.close()

        owner_session = sessions()
        other_session = sessions()
        try:
            await owner_session.begin()
            await other_session.begin()
            await PlansService(repository=PlansRepository(owner_session)).create_task(
                owner_user_id=owner_user_id,
                task_date=date(2026, 7, 23),
                task_time="08:00",
                title="owner lock",
            )
            other_created = await asyncio.wait_for(
                PlansService(repository=PlansRepository(other_session)).create_task(
                    owner_user_id=other_user_id,
                    task_date=date(2026, 7, 23),
                    task_time="08:00",
                    title="other owner",
                ),
                timeout=1,
            )
            assert other_created.owner_user_id == other_user_id
            await other_session.commit()
            await owner_session.rollback()
        finally:
            if owner_session.in_transaction():
                await owner_session.rollback()
            if other_session.in_transaction():
                await other_session.rollback()
            await owner_session.close()
            await other_session.close()
    finally:
        await engine.dispose()
        cleanup_engine = create_async_engine(DATABASE_URL)
        async with cleanup_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await cleanup_engine.dispose()
