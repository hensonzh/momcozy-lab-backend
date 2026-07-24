from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Plan, PlanTask


class PlansRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_plan(
        self,
        *,
        owner_user_id: UUID,
        plan_type: str,
        title: str,
        summary: str,
        source: str,
        payload: dict[str, Any],
    ) -> Plan:
        plan = Plan(
            owner_user_id=owner_user_id,
            plan_type=plan_type,
            title=title,
            summary=summary,
            source=source,
            payload=payload,
        )
        self.session.add(plan)
        await self.session.flush()
        return plan

    async def get_plan_for_owner(self, *, plan_id: UUID, owner_user_id: UUID) -> Plan | None:
        statement = select(Plan).where(Plan.id == plan_id, Plan.owner_user_id == owner_user_id, Plan.deleted_at.is_(None))
        return cast(Plan | None, await self.session.scalar(statement))

    async def get_plan_for_owner_for_update(self, *, plan_id: UUID, owner_user_id: UUID) -> Plan | None:
        statement = (
            select(Plan)
            .where(
                Plan.id == plan_id,
                Plan.owner_user_id == owner_user_id,
                Plan.deleted_at.is_(None),
            )
            .with_for_update()
        )
        return cast(Plan | None, await self.session.scalar(statement))

    async def update_plan_payload_and_version(
        self,
        *,
        plan_id: UUID,
        owner_user_id: UUID,
        expected_version: int,
        payload: dict[str, Any],
    ) -> Plan | None:
        plan = await self.get_plan_for_owner_for_update(plan_id=plan_id, owner_user_id=owner_user_id)
        if plan is None or plan.version != expected_version:
            return None
        plan.payload = payload
        plan.version += 1
        await self.session.flush()
        return plan

    async def list_plans(self, *, owner_user_id: UUID, plan_type: str, status: str, limit: int) -> list[Plan]:
        statement = select(Plan).where(Plan.owner_user_id == owner_user_id, Plan.status == status, Plan.deleted_at.is_(None))
        if plan_type:
            statement = statement.where(Plan.plan_type == plan_type)
        statement = statement.order_by(Plan.updated_at.desc(), Plan.id.desc()).limit(limit)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def soft_delete_plan(self, *, plan_id: UUID, owner_user_id: UUID, deleted_at: datetime) -> Plan | None:
        plan = await self.get_plan_for_owner(plan_id=plan_id, owner_user_id=owner_user_id)
        if plan is None:
            return None
        plan.status = "deleted"
        plan.deleted_at = deleted_at
        await self.session.flush()
        return plan

    async def create_task(
        self,
        *,
        owner_user_id: UUID,
        plan_id: UUID | None,
        task_date: date | None,
        task_time: str,
        title: str,
        description: str,
        payload: dict[str, Any],
    ) -> PlanTask:
        task = PlanTask(
            owner_user_id=owner_user_id,
            plan_id=plan_id,
            task_date=task_date,
            task_time=task_time,
            title=title,
            description=description,
            payload=payload,
        )
        self.session.add(task)
        await self.session.flush()
        return task

    async def get_task_for_owner(self, *, task_id: UUID, owner_user_id: UUID) -> PlanTask | None:
        statement = select(PlanTask).where(
            PlanTask.id == task_id,
            PlanTask.owner_user_id == owner_user_id,
            PlanTask.deleted_at.is_(None),
        )
        return cast(PlanTask | None, await self.session.scalar(statement))

    async def get_task_for_owner_for_update(self, *, task_id: UUID, owner_user_id: UUID) -> PlanTask | None:
        statement = (
            select(PlanTask)
            .where(
                PlanTask.id == task_id,
                PlanTask.owner_user_id == owner_user_id,
                PlanTask.deleted_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return cast(PlanTask | None, await self.session.scalar(statement))

    async def list_tasks_for_milk_reschedule_for_update(
        self,
        *,
        owner_user_id: UUID,
        task_ids: list[UUID],
        task_dates: list[date],
    ) -> list[PlanTask]:
        statement = (
            select(PlanTask)
            .where(
                PlanTask.owner_user_id == owner_user_id,
                PlanTask.deleted_at.is_(None),
                or_(PlanTask.id.in_(task_ids), PlanTask.task_date.in_(task_dates)),
            )
            .order_by(PlanTask.id.asc())
            .with_for_update()
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def lock_milk_schedule_dates(self, *, owner_user_id: UUID, task_dates: list[date]) -> None:
        for task_date in sorted(set(task_dates)):
            lock_key = _schedule_advisory_lock_key(owner_user_id=owner_user_id, task_date=task_date)
            await self.session.execute(select(func.pg_advisory_xact_lock(lock_key)))

    async def list_tasks(
        self,
        *,
        owner_user_id: UUID,
        task_date: date | None,
        status: str | None,
        limit: int,
    ) -> list[PlanTask]:
        conditions = [PlanTask.owner_user_id == owner_user_id, PlanTask.deleted_at.is_(None)]
        if task_date is not None:
            conditions.append(PlanTask.task_date == task_date)
        if status is not None:
            conditions.append(PlanTask.status == status)
        statement = select(PlanTask).where(*conditions).order_by(PlanTask.task_date.asc(), PlanTask.task_time.asc()).limit(limit)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def list_tasks_for_plan(
        self,
        *,
        owner_user_id: UUID,
        plan_id: UUID,
        task_dates: list[date] | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[PlanTask]:
        conditions = [
            PlanTask.owner_user_id == owner_user_id,
            PlanTask.plan_id == plan_id,
            PlanTask.deleted_at.is_(None),
        ]
        if task_dates:
            conditions.append(PlanTask.task_date.in_(task_dates))
        if status is not None:
            conditions.append(PlanTask.status == status)
        statement = (
            select(PlanTask).where(*conditions).order_by(PlanTask.task_date.asc(), PlanTask.task_time.asc(), PlanTask.id.asc()).limit(limit)
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def list_future_milk_plan_tasks(
        self,
        *,
        owner_user_id: UUID,
        start_date: date,
        end_date: date,
        for_update: bool,
        limit: int,
    ) -> list[PlanTask]:
        statement = (
            select(PlanTask)
            .join(Plan, Plan.id == PlanTask.plan_id)
            .where(
                PlanTask.owner_user_id == owner_user_id,
                PlanTask.deleted_at.is_(None),
                PlanTask.status == "pending",
                PlanTask.task_date >= start_date,
                PlanTask.task_date <= end_date,
                Plan.owner_user_id == owner_user_id,
                Plan.plan_type == "milk_management",
                Plan.status == "active",
                Plan.deleted_at.is_(None),
            )
            .order_by(PlanTask.task_date.asc(), PlanTask.task_time.asc(), PlanTask.id.asc())
            .limit(limit)
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self.session.scalars(statement)
        return list(result.all())

    async def list_milk_timeline_tasks(
        self,
        *,
        owner_user_id: UUID,
        start_date: date,
        end_date: date,
        limit: int,
    ) -> list[PlanTask]:
        statement = (
            select(PlanTask)
            .join(Plan, Plan.id == PlanTask.plan_id)
            .where(
                PlanTask.owner_user_id == owner_user_id,
                PlanTask.deleted_at.is_(None),
                PlanTask.task_date >= start_date,
                PlanTask.task_date <= end_date,
                Plan.owner_user_id == owner_user_id,
                Plan.plan_type == "milk_management",
                Plan.deleted_at.is_(None),
            )
            .order_by(PlanTask.task_date.asc(), PlanTask.task_time.asc(), PlanTask.id.asc())
            .limit(limit)
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def set_task_completed(
        self,
        *,
        task_id: UUID,
        owner_user_id: UUID,
        completed: bool,
        completed_at: datetime | None,
    ) -> PlanTask | None:
        task = await self.get_task_for_owner(task_id=task_id, owner_user_id=owner_user_id)
        if task is None:
            return None
        task.status = "completed" if completed else "pending"
        task.completed_at = completed_at
        await self.session.flush()
        return task

    async def set_task_state(
        self,
        *,
        task_id: UUID,
        owner_user_id: UUID,
        state: str,
        completed_at: datetime | None,
    ) -> PlanTask | None:
        task = await self.get_task_for_owner(task_id=task_id, owner_user_id=owner_user_id)
        if task is None:
            return None
        task.status = state
        task.completed_at = completed_at
        await self.session.flush()
        return task

    async def update_task(
        self,
        *,
        task_id: UUID,
        owner_user_id: UUID,
        updates: dict[str, Any],
    ) -> PlanTask | None:
        task = await self.get_task_for_owner(task_id=task_id, owner_user_id=owner_user_id)
        if task is None:
            return None
        for field, value in updates.items():
            setattr(task, field, value)
        await self.session.flush()
        return task

    async def soft_delete_task(self, *, task_id: UUID, owner_user_id: UUID, deleted_at: datetime) -> PlanTask | None:
        task = await self.get_task_for_owner(task_id=task_id, owner_user_id=owner_user_id)
        if task is None:
            return None
        task.status = "deleted"
        task.deleted_at = deleted_at
        await self.session.flush()
        return task


def _schedule_advisory_lock_key(*, owner_user_id: UUID, task_date: date) -> int:
    digest = hashlib.sha256(f"plan-task-schedule:{owner_user_id}:{task_date.isoformat()}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)
