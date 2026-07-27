from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Plan, PlanTask
from .schedule_domain import SPECIALIZED_PLAN_TYPES


@dataclass(frozen=True)
class ScheduleTimelineTaskRow:
    task: PlanTask
    plan: Plan | None


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
        starts_on: date | None = None,
        ends_on: date | None = None,
    ) -> Plan:
        plan = Plan(
            owner_user_id=owner_user_id,
            plan_type=plan_type,
            title=title,
            summary=summary,
            source=source,
            payload=payload,
            starts_on=starts_on,
            ends_on=ends_on,
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

    async def lock_plan_type(self, *, owner_user_id: UUID, plan_type: str) -> None:
        lock_key = _plan_type_advisory_lock_key(
            owner_user_id=owner_user_id,
            plan_type=plan_type,
        )
        await self.session.execute(select(func.pg_advisory_xact_lock(lock_key)))

    async def get_active_plan_by_type_for_update(
        self,
        *,
        owner_user_id: UUID,
        plan_type: str,
    ) -> Plan | None:
        statement = (
            select(Plan)
            .where(
                Plan.owner_user_id == owner_user_id,
                Plan.plan_type == plan_type,
                Plan.status == "active",
                Plan.deleted_at.is_(None),
            )
            .order_by(Plan.updated_at.desc(), Plan.id.desc())
            .limit(1)
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

    async def update_plan_metadata_and_version(
        self,
        *,
        plan_id: UUID,
        owner_user_id: UUID,
        expected_version: int,
        updates: dict[str, Any],
    ) -> Plan | None:
        plan = await self.get_plan_for_owner_for_update(
            plan_id=plan_id,
            owner_user_id=owner_user_id,
        )
        if plan is None or plan.version != expected_version:
            return None
        for field, value in updates.items():
            setattr(plan, field, value)
        plan.version += 1
        await self.session.flush()
        return plan

    async def list_plans(
        self,
        *,
        owner_user_id: UUID,
        plan_type: str,
        status: str,
        as_of_date: date,
        limit: int,
    ) -> list[Plan]:
        statement = select(Plan).where(
            Plan.owner_user_id == owner_user_id,
            Plan.status == status,
            Plan.deleted_at.is_(None),
        )
        if status == "active":
            statement = statement.where(
                or_(Plan.ends_on.is_(None), Plan.ends_on >= as_of_date)
            )
        if plan_type:
            statement = statement.where(Plan.plan_type == plan_type)
        statement = statement.order_by(Plan.updated_at.desc(), Plan.id.desc()).limit(limit)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def list_schedule_timeline_plans(
        self,
        *,
        owner_user_id: UUID,
        domains: tuple[str, ...],
        status: str,
        as_of_date: date,
        limit: int,
    ) -> list[Plan]:
        statement = (
            select(Plan)
            .where(
                Plan.owner_user_id == owner_user_id,
                Plan.status == status,
                Plan.deleted_at.is_(None),
                _plan_domain_condition(domains),
            )
        )
        if status == "active":
            statement = statement.where(
                or_(Plan.ends_on.is_(None), Plan.ends_on >= as_of_date)
            )
        statement = statement.order_by(
            Plan.updated_at.desc(),
            Plan.id.desc(),
        ).limit(limit)
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

    async def soft_delete_tasks_for_plan(
        self,
        *,
        plan_id: UUID,
        owner_user_id: UUID,
        deleted_at: datetime,
    ) -> list[PlanTask]:
        statement = (
            select(PlanTask)
            .where(
                PlanTask.plan_id == plan_id,
                PlanTask.owner_user_id == owner_user_id,
                PlanTask.deleted_at.is_(None),
            )
            .with_for_update()
        )
        tasks = list((await self.session.scalars(statement)).all())
        for task in tasks:
            task.status = "deleted"
            task.deleted_at = deleted_at
        await self.session.flush()
        return tasks

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
        plan_type: str = "",
    ) -> list[PlanTask]:
        conditions = [PlanTask.owner_user_id == owner_user_id, PlanTask.deleted_at.is_(None)]
        if task_date is not None:
            conditions.append(PlanTask.task_date == task_date)
        if status is not None:
            conditions.append(PlanTask.status == status)
        if plan_type:
            conditions.append(
                PlanTask.plan_id.in_(
                    select(Plan.id).where(
                        Plan.owner_user_id == owner_user_id,
                        Plan.plan_type == plan_type,
                        Plan.status == "active",
                        Plan.deleted_at.is_(None),
                    )
                )
            )
        statement = select(PlanTask).where(*conditions).order_by(PlanTask.task_date.asc(), PlanTask.task_time.asc()).limit(limit)
        result = await self.session.scalars(statement)
        return list(result.all())

    async def list_schedule_timeline_tasks(
        self,
        *,
        owner_user_id: UUID,
        start_date: date,
        end_date: date,
        domains: tuple[str, ...],
        limit: int,
    ) -> list[ScheduleTimelineTaskRow]:
        statement = (
            select(PlanTask, Plan)
            .outerjoin(
                Plan,
                and_(
                    Plan.id == PlanTask.plan_id,
                    Plan.owner_user_id == owner_user_id,
                ),
            )
            .where(
                PlanTask.owner_user_id == owner_user_id,
                PlanTask.deleted_at.is_(None),
                PlanTask.task_date >= start_date,
                PlanTask.task_date <= end_date,
                or_(
                    PlanTask.plan_id.is_(None),
                    and_(Plan.id.is_not(None), Plan.deleted_at.is_(None)),
                ),
                _task_domain_condition(domains),
            )
            .order_by(PlanTask.task_date.asc(), PlanTask.task_time.asc(), PlanTask.id.asc())
            .limit(limit)
        )
        result = await self.session.execute(statement)
        return [
            ScheduleTimelineTaskRow(task=row[0], plan=row[1])
            for row in result.all()
        ]

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


def _plan_type_advisory_lock_key(*, owner_user_id: UUID, plan_type: str) -> int:
    digest = hashlib.sha256(
        f"plan-type:{owner_user_id}:{plan_type}".encode()
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _plan_domain_condition(domains: tuple[str, ...]) -> Any:
    conditions: list[Any] = []
    if "lactation" in domains:
        conditions.append(Plan.plan_type == "milk_management")
    if "pregnancy" in domains:
        conditions.append(Plan.plan_type.in_(("pregnancy", "birth_prep", "birth_journey")))
    if "postpartum_recovery" in domains:
        conditions.append(Plan.plan_type == "postpartum_recovery")
    if "general" in domains:
        conditions.append(Plan.plan_type.not_in(SPECIALIZED_PLAN_TYPES))
    return or_(*conditions)


def _task_domain_condition(domains: tuple[str, ...]) -> Any:
    standalone_domain = PlanTask.payload["domain"].astext
    conditions: list[Any] = []
    if "lactation" in domains:
        conditions.append(
            or_(
                Plan.plan_type == "milk_management",
                and_(PlanTask.plan_id.is_(None), standalone_domain == "lactation"),
            )
        )
    if "pregnancy" in domains:
        conditions.append(
            or_(
                Plan.plan_type.in_(("pregnancy", "birth_prep", "birth_journey")),
                and_(PlanTask.plan_id.is_(None), standalone_domain == "pregnancy"),
            )
        )
    if "postpartum_recovery" in domains:
        conditions.append(
            or_(
                Plan.plan_type == "postpartum_recovery",
                and_(PlanTask.plan_id.is_(None), standalone_domain == "postpartum_recovery"),
            )
        )
    if "general" in domains:
        conditions.append(
            or_(
                Plan.plan_type.not_in(SPECIALIZED_PLAN_TYPES),
                and_(
                    PlanTask.plan_id.is_(None),
                    or_(
                        standalone_domain.is_(None),
                        standalone_domain.not_in(("lactation", "pregnancy", "postpartum_recovery")),
                    ),
                ),
            )
        )
    return or_(*conditions)
