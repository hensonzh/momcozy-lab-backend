from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ..audit import AuditService, IdempotencyKey, IdempotencyService, parse_idempotency_response_ref, request_hash
from .models import Plan, PlanTask
from .pregnancy_plan_todos import normalize_pregnancy_plan_payload
from .repository import PlansRepository, ScheduleTimelineTaskRow
from .schedule_domain import normalize_schedule_domains


PLAN_CREATE_IDEMPOTENCY_SCOPE = "plans.create"
PLAN_TASK_CREATE_IDEMPOTENCY_SCOPE = "plans.tasks.create"
PLAN_TODO_COMPLETION_IDEMPOTENCY_SCOPE = "plans.todos.completion"
PLAN_TASK_STATES = frozenset({"pending", "completed", "skipped"})
MAX_MILK_SCHEDULE_RESCHEDULE_TASKS = 100


class PlansService:
    def __init__(
        self,
        *,
        repository: PlansRepository,
        audit_service: AuditService | None = None,
        idempotency_service: IdempotencyService | None = None,
    ) -> None:
        self.repository = repository
        self.audit_service = audit_service
        self.idempotency_service = idempotency_service

    async def create_plan(
        self,
        *,
        owner_user_id: UUID,
        plan_type: str = "",
        title: str,
        summary: str = "",
        source: str = "manual",
        payload: dict[str, Any] | None = None,
        request_id: str = "",
        idempotency_key: str | None = None,
    ) -> Plan:
        if plan_type.strip().lower() == "milk_management":
            raise ApiError(
                code="unsupported_plan_type",
                message="Creating milk-management plans is no longer supported.",
                status=422,
            )
        normalized_payload = _normalize_plan_payload(plan_type=plan_type, payload=payload or {})
        starts_on, ends_on = _plan_effective_dates(normalized_payload)
        idempotency_record = await self._reserve_idempotency(
            owner_user_id=owner_user_id,
            scope=PLAN_CREATE_IDEMPOTENCY_SCOPE,
            key=idempotency_key,
            payload={"plan_type": plan_type, "title": title, "summary": summary, "source": source, "payload": normalized_payload},
        )
        if idempotency_record is not None and idempotency_record.response_ref:
            return await self._replay_plan(owner_user_id=owner_user_id, response_ref=idempotency_record.response_ref)

        if plan_type.strip().lower() == "pregnancy":
            lock_plan_type = getattr(self.repository, "lock_plan_type", None)
            if callable(lock_plan_type):
                await lock_plan_type(
                    owner_user_id=owner_user_id,
                    plan_type="pregnancy",
                )
            active_plan = await self.repository.get_active_plan_by_type_for_update(
                owner_user_id=owner_user_id,
                plan_type="pregnancy",
            )
            if active_plan is not None:
                raise ApiError(
                    code="active_pregnancy_plan_exists",
                    message="An active pregnancy plan already exists. Update or delete it before creating another.",
                    status=409,
                    details={
                        "plan_id": str(active_plan.id),
                        "version": active_plan.version,
                    },
                )
        plan = await self.repository.create_plan(
            owner_user_id=owner_user_id,
            plan_type=plan_type,
            title=title.strip(),
            summary=summary,
            source=source,
            payload=normalized_payload,
            starts_on=starts_on,
            ends_on=ends_on,
        )
        await self._complete_idempotency(idempotency_record=idempotency_record, response_ref=str(plan.id))
        await self._audit(
            owner_user_id=owner_user_id, action="plans.create", resource_type="plan", resource_id=str(plan.id), request_id=request_id
        )
        return plan

    async def list_plans(
        self,
        *,
        owner_user_id: UUID,
        plan_type: str = "",
        status: str = "active",
        as_of_date: date | None = None,
        limit: int = 50,
    ) -> list[Plan]:
        self._validate_limit(limit)
        return await self.repository.list_plans(
            owner_user_id=owner_user_id,
            plan_type=plan_type.strip(),
            status=status,
            as_of_date=as_of_date or _utcnow().date(),
            limit=limit,
        )

    async def list_schedule_timeline_plans(
        self,
        *,
        owner_user_id: UUID,
        domains: tuple[str, ...],
        status: str = "active",
        as_of_date: date | None = None,
        limit: int = 20,
    ) -> list[Plan]:
        normalized_domains = normalize_schedule_domains(domains)
        self._validate_limit(limit)
        return await self.repository.list_schedule_timeline_plans(
            owner_user_id=owner_user_id,
            domains=normalized_domains,
            status=status,
            as_of_date=as_of_date or _utcnow().date(),
            limit=limit,
        )

    async def get_plan(self, *, owner_user_id: UUID, plan_id: UUID) -> Plan:
        plan = await self.repository.get_plan_for_owner(plan_id=plan_id, owner_user_id=owner_user_id)
        if plan is None:
            raise ApiError(code="not_found", message="Plan not found.", status=404)
        return plan

    async def update_plan_metadata(
        self,
        *,
        owner_user_id: UUID,
        plan_id: UUID,
        expected_version: int,
        updates: dict[str, Any],
        request_id: str = "",
    ) -> Plan:
        unsupported = set(updates) - {"title", "summary"}
        if unsupported or not updates:
            raise ApiError(code="validation_failed", message="Unsupported plan update fields.", status=422)
        normalized: dict[str, str] = {}
        if "title" in updates:
            title = str(updates["title"] or "").strip()
            if not title or len(title) > 255:
                raise ApiError(code="validation_failed", message="title is invalid.", status=422)
            normalized["title"] = title
        if "summary" in updates:
            summary = updates["summary"]
            if not isinstance(summary, str):
                raise ApiError(code="validation_failed", message="summary is invalid.", status=422)
            normalized["summary"] = summary.strip()
        updated = await self.repository.update_plan_metadata_and_version(
            plan_id=plan_id,
            owner_user_id=owner_user_id,
            expected_version=expected_version,
            updates=normalized,
        )
        if updated is None:
            existing = await self.repository.get_plan_for_owner(
                plan_id=plan_id,
                owner_user_id=owner_user_id,
            )
            if existing is None:
                raise ApiError(code="not_found", message="Plan not found.", status=404)
            raise ApiError(code="version_conflict", message="Plan version changed.", status=409)
        await self._audit(
            owner_user_id=owner_user_id,
            action="plans.update",
            resource_type="plan",
            resource_id=str(plan_id),
            request_id=request_id,
            details={"fields": sorted(normalized), "version": updated.version},
        )
        return updated

    async def delete_plan(
        self,
        *,
        owner_user_id: UUID,
        plan_id: UUID,
        request_id: str = "",
        reason: str = "",
    ) -> None:
        plan = await self.repository.get_plan_for_owner_for_update(
            plan_id=plan_id,
            owner_user_id=owner_user_id,
        )
        if plan is None:
            raise ApiError(code="not_found", message="Plan not found.", status=404)
        deleted_at = _utcnow()
        delete_tasks = getattr(self.repository, "soft_delete_tasks_for_plan", None)
        deleted_tasks = (
            await delete_tasks(
                plan_id=plan_id,
                owner_user_id=owner_user_id,
                deleted_at=deleted_at,
            )
            if callable(delete_tasks)
            else []
        )
        deleted = await self.repository.soft_delete_plan(
            plan_id=plan_id,
            owner_user_id=owner_user_id,
            deleted_at=deleted_at,
        )
        if deleted is None:
            raise ApiError(code="not_found", message="Plan not found.", status=404)
        await self._audit(
            owner_user_id=owner_user_id,
            action="plans.delete",
            resource_type="plan",
            resource_id=str(plan_id),
            request_id=request_id,
            details={
                "deleted_task_count": len(deleted_tasks),
                **({"reason": reason.strip()} if reason.strip() else {}),
            },
        )

    async def create_task(
        self,
        *,
        owner_user_id: UUID,
        plan_id: UUID | None = None,
        task_date: date | None = None,
        task_time: str = "",
        title: str,
        description: str = "",
        payload: dict[str, Any] | None = None,
        request_id: str = "",
        idempotency_key: str | None = None,
    ) -> PlanTask:
        if plan_id is not None and await self.repository.get_plan_for_owner(plan_id=plan_id, owner_user_id=owner_user_id) is None:
            raise ApiError(code="owner_scope_violation", message="Plan is outside the current user scope.", status=403)
        idempotency_record = await self._reserve_idempotency(
            owner_user_id=owner_user_id,
            scope=PLAN_TASK_CREATE_IDEMPOTENCY_SCOPE,
            key=idempotency_key,
            payload={
                "plan_id": str(plan_id or ""),
                "task_date": str(task_date or ""),
                "task_time": task_time,
                "title": title,
                "description": description,
                "payload": payload or {},
            },
        )
        if idempotency_record is not None and idempotency_record.response_ref:
            return await self._replay_task(owner_user_id=owner_user_id, response_ref=idempotency_record.response_ref)

        await self._lock_task_schedule_dates(
            owner_user_id=owner_user_id,
            task_dates=[task_date] if task_date is not None else [],
        )
        task = await self.repository.create_task(
            owner_user_id=owner_user_id,
            plan_id=plan_id,
            task_date=task_date,
            task_time=task_time,
            title=title.strip(),
            description=description,
            payload=payload or {},
        )
        await self._complete_idempotency(idempotency_record=idempotency_record, response_ref=str(task.id))
        await self._audit(
            owner_user_id=owner_user_id,
            action="plans.tasks.create",
            resource_type="plan_task",
            resource_id=str(task.id),
            request_id=request_id,
        )
        return task

    async def list_tasks(
        self,
        *,
        owner_user_id: UUID,
        task_date: date | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[PlanTask]:
        self._validate_limit(limit)
        return await self.repository.list_tasks(owner_user_id=owner_user_id, task_date=task_date, status=status, limit=limit)

    async def list_schedule_timeline_tasks(
        self,
        *,
        owner_user_id: UUID,
        start_date: date,
        end_date: date,
        domains: tuple[str, ...],
        limit: int,
    ) -> list[ScheduleTimelineTaskRow]:
        window_days = (end_date - start_date).days + 1
        if window_days < 1 or window_days > 31:
            raise ApiError(
                code="validation_failed",
                message="Timeline date range must contain 1 to 31 days.",
                status=422,
            )
        normalized_domains = normalize_schedule_domains(domains)
        self._validate_limit(limit)
        return await self.repository.list_schedule_timeline_tasks(
            owner_user_id=owner_user_id,
            start_date=start_date,
            end_date=end_date,
            domains=normalized_domains,
            limit=limit,
        )

    async def get_task(self, *, owner_user_id: UUID, task_id: UUID) -> PlanTask:
        task = await self.repository.get_task_for_owner(task_id=task_id, owner_user_id=owner_user_id)
        if task is None:
            raise ApiError(code="not_found", message="Plan task not found.", status=404)
        return task

    async def list_tasks_for_plan(
        self,
        *,
        owner_user_id: UUID,
        plan_id: UUID,
        task_dates: list[date] | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[PlanTask]:
        if limit < 1 or limit > 500:
            raise ApiError(code="validation_failed", message="limit must be between 1 and 500.", status=422)
        await self.get_plan(owner_user_id=owner_user_id, plan_id=plan_id)
        return await self.repository.list_tasks_for_plan(
            owner_user_id=owner_user_id,
            plan_id=plan_id,
            task_dates=task_dates,
            status=status,
            limit=limit,
        )

    async def reschedule_milk_tasks(
        self,
        *,
        owner_user_id: UUID,
        plan_id: UUID,
        updates: list[dict[str, Any]],
        request_id: str = "",
    ) -> list[PlanTask]:
        if not updates or len(updates) > MAX_MILK_SCHEDULE_RESCHEDULE_TASKS:
            raise ApiError(code="validation_failed", message="A bounded list of schedule updates is required.", status=422)
        plan = await self.get_plan(owner_user_id=owner_user_id, plan_id=plan_id)
        if plan.plan_type != "milk_management":
            raise ApiError(code="validation_failed", message="Plan is not a milk-management plan.", status=422)

        parsed_updates: list[dict[str, Any]] = []
        seen_task_ids: set[UUID] = set()
        for update in updates:
            task_id = _required_uuid_value(update.get("task_id"), code="invalid_task_id")
            if task_id in seen_task_ids:
                raise ApiError(code="validation_failed", message="Duplicate task update.", status=422)
            seen_task_ids.add(task_id)
            expected_plan_id = _required_uuid_value(update.get("expected_plan_id"), code="invalid_expected_plan_id")
            expected_date = _required_date_value(update.get("expected_task_date"), code="invalid_expected_task_date")
            expected_time = _required_time_value(update.get("expected_task_time"), code="invalid_expected_task_time")
            new_date = _required_date_value(update.get("new_task_date"), code="invalid_new_task_date")
            new_time = _required_time_value(update.get("new_task_time"), code="invalid_new_task_time")
            parsed_updates.append(
                {
                    "task_id": task_id,
                    "expected_plan_id": expected_plan_id,
                    "expected_date": expected_date,
                    "expected_time": expected_time,
                    "new_date": new_date,
                    "new_time": new_time,
                }
            )

        affected_dates = sorted(
            {item["expected_date"] for item in parsed_updates} | {item["new_date"] for item in parsed_updates}
        )
        await self.repository.lock_milk_schedule_dates(
            owner_user_id=owner_user_id,
            task_dates=affected_dates,
        )
        locked_tasks = await self.repository.list_tasks_for_milk_reschedule_for_update(
            owner_user_id=owner_user_id,
            task_ids=sorted(seen_task_ids, key=str),
            task_dates=affected_dates,
        )
        locked_by_id = {task.id: task for task in locked_tasks}
        tasks_to_move: list[tuple[PlanTask, date, str]] = []
        for update in parsed_updates:
            task = locked_by_id.get(update["task_id"])
            if task is None:
                raise ApiError(code="not_found", message="Plan task not found.", status=404)
            if task.plan_id != plan_id or task.status != "pending":
                raise ApiError(code="owner_scope_violation", message="Task is outside the requested active milk plan.", status=403)
            if (
                update["expected_plan_id"] != plan_id
                or task.task_date != update["expected_date"]
                or task.task_time != update["expected_time"]
            ):
                raise ApiError(
                    code="milk_schedule_conflict",
                    message="The milk schedule changed after preview. Create a fresh preview before applying.",
                    status=409,
                )
            tasks_to_move.append((task, update["new_date"], update["new_time"]))

        if _milk_schedule_has_target_conflict(tasks_to_move=tasks_to_move, locked_tasks=locked_tasks):
            raise ApiError(
                code="milk_schedule_conflict",
                message="A target milk-schedule slot is no longer available. Create a fresh preview before applying.",
                status=409,
            )

        applied: list[PlanTask] = []
        for task, new_date, new_time in tasks_to_move:
            updated = await self.repository.update_task(
                task_id=task.id,
                owner_user_id=owner_user_id,
                updates={"task_date": new_date, "task_time": new_time},
            )
            if updated is None:
                raise ApiError(code="milk_schedule_conflict", message="A milk-plan task changed during apply.", status=409)
            applied.append(updated)
            await self._audit(
                owner_user_id=owner_user_id,
                action="plans.milk_schedule.reschedule",
                resource_type="plan_task",
                resource_id=str(updated.id),
                request_id=request_id,
            )
        return applied

    async def set_task_completed(self, *, owner_user_id: UUID, task_id: UUID, completed: bool, request_id: str = "") -> PlanTask:
        existing = await self.get_task(owner_user_id=owner_user_id, task_id=task_id)
        pregnancy_plan = await self._lock_linked_pregnancy_plan(
            owner_user_id=owner_user_id,
            task=existing,
        )
        task = await self.repository.set_task_completed(
            task_id=task_id,
            owner_user_id=owner_user_id,
            completed=completed,
            completed_at=_utcnow() if completed else None,
        )
        if task is None:
            raise ApiError(code="not_found", message="Plan task not found.", status=404)
        if pregnancy_plan is not None:
            await self._sync_pregnancy_todo_from_task(
                owner_user_id=owner_user_id,
                plan=pregnancy_plan,
                task=task,
                state="completed" if completed else "pending",
            )
        await self._audit(
            owner_user_id=owner_user_id,
            action="plans.tasks.complete",
            resource_type="plan_task",
            resource_id=str(task_id),
            request_id=request_id,
        )
        return task

    async def set_task_state(
        self,
        *,
        owner_user_id: UUID,
        task_id: UUID,
        state: str,
        request_id: str = "",
    ) -> PlanTask:
        normalized_state = state.strip().lower()
        if normalized_state not in PLAN_TASK_STATES:
            raise ApiError(
                code="validation_failed",
                message="state must be pending, completed, or skipped.",
                status=422,
            )
        existing = await self.get_task(owner_user_id=owner_user_id, task_id=task_id)
        pregnancy_plan = await self._lock_linked_pregnancy_plan(
            owner_user_id=owner_user_id,
            task=existing,
        )
        task = await self.repository.set_task_state(
            task_id=task_id,
            owner_user_id=owner_user_id,
            state=normalized_state,
            completed_at=_utcnow() if normalized_state == "completed" else None,
        )
        if task is None:
            raise ApiError(code="not_found", message="Plan task not found.", status=404)
        if pregnancy_plan is not None:
            await self._sync_pregnancy_todo_from_task(
                owner_user_id=owner_user_id,
                plan=pregnancy_plan,
                task=task,
                state=normalized_state,
            )
        await self._audit(
            owner_user_id=owner_user_id,
            action="plans.tasks.state",
            resource_type="plan_task",
            resource_id=str(task_id),
            request_id=request_id,
        )
        return task

    async def update_plan_todo_completion(
        self,
        *,
        owner_user_id: UUID,
        plan_id: UUID,
        item_id: str,
        completed: bool,
        expected_version: int,
        request_id: str = "",
        idempotency_key: str | None = None,
    ) -> Plan:
        normalized_item_id = item_id.strip()
        if not normalized_item_id:
            raise ApiError(code="validation_failed", message="item_id is required.", status=422)
        idempotency_record = await self._reserve_idempotency(
            owner_user_id=owner_user_id,
            scope=PLAN_TODO_COMPLETION_IDEMPOTENCY_SCOPE,
            key=idempotency_key,
            payload={
                "plan_id": str(plan_id),
                "item_id": normalized_item_id,
                "completed": completed,
                "expected_version": expected_version,
            },
        )
        if idempotency_record is not None and idempotency_record.response_ref:
            return await self._replay_plan(owner_user_id=owner_user_id, response_ref=idempotency_record.response_ref)

        plan = await self.repository.get_plan_for_owner_for_update(
            plan_id=plan_id,
            owner_user_id=owner_user_id,
        )
        if plan is None:
            raise ApiError(code="not_found", message="Plan not found.", status=404)
        if plan.plan_type != "pregnancy":
            raise ApiError(code="validation_failed", message="Plan is not a pregnancy plan.", status=422)
        if plan.version != expected_version:
            raise ApiError(
                code="version_conflict",
                message="Plan was updated by another request.",
                status=409,
                details={"expected_version": expected_version, "current_version": plan.version},
            )

        next_payload = deepcopy(plan.payload)
        item = _find_todo_item_by_item_id(next_payload, normalized_item_id)
        if item is None:
            raise ApiError(
                code="todo_item_not_found",
                message="Plan todo item was not found or does not have a stable item_id.",
                status=404,
            )
        item["completed"] = completed
        item["status"] = "completed" if completed else "pending"
        updated = await self.repository.update_plan_payload_and_version(
            plan_id=plan_id,
            owner_user_id=owner_user_id,
            expected_version=expected_version,
            payload=next_payload,
        )
        if updated is None:
            raise ApiError(code="version_conflict", message="Plan was updated by another request.", status=409)
        linked_tasks = await self.repository.list_tasks_for_plan(
            owner_user_id=owner_user_id,
            plan_id=plan_id,
            task_dates=None,
            status=None,
            limit=500,
        )
        completed_at = _utcnow() if completed else None
        synced_task_count = 0
        for task in linked_tasks:
            if not _task_links_to_pregnancy_todo(task, item_id=normalized_item_id):
                continue
            synced = await self.repository.set_task_state(
                task_id=task.id,
                owner_user_id=owner_user_id,
                state="completed" if completed else "pending",
                completed_at=completed_at,
            )
            if synced is None:
                raise ApiError(
                    code="plan_task_sync_conflict",
                    message="A linked plan task changed during todo update.",
                    status=409,
                )
            synced_task_count += 1
        await self._complete_idempotency(idempotency_record=idempotency_record, response_ref=str(plan_id))
        await self._audit(
            owner_user_id=owner_user_id,
            action="plans.todos.completion",
            resource_type="plan",
            resource_id=str(plan_id),
            request_id=request_id,
            details={"synced_task_count": synced_task_count, "version": updated.version},
        )
        return updated

    async def update_task(
        self,
        *,
        owner_user_id: UUID,
        task_id: UUID,
        updates: dict[str, Any],
        request_id: str = "",
    ) -> PlanTask:
        allowed_fields = {"plan_id", "task_date", "task_time", "title", "description", "payload"}
        unknown_fields = set(updates) - allowed_fields
        if unknown_fields:
            raise ApiError(code="validation_failed", message="Unsupported plan task update fields.", status=422)
        if not updates:
            raise ApiError(code="validation_failed", message="At least one task field is required.", status=422)
        if "plan_id" in updates and updates["plan_id"] is not None:
            plan = await self.repository.get_plan_for_owner(plan_id=updates["plan_id"], owner_user_id=owner_user_id)
            if plan is None:
                raise ApiError(code="owner_scope_violation", message="Plan is outside the current user scope.", status=403)

        normalized_updates = dict(updates)
        if "task_time" in normalized_updates:
            normalized_updates["task_time"] = normalized_updates["task_time"] or ""
        if "description" in normalized_updates:
            normalized_updates["description"] = normalized_updates["description"] or ""
        if "payload" in normalized_updates:
            normalized_updates["payload"] = normalized_updates["payload"] or {}
        if "title" in normalized_updates:
            normalized_updates["title"] = normalized_updates["title"].strip()
            if not normalized_updates["title"]:
                raise ApiError(code="validation_failed", message="title is required.", status=422)

        task_before_update: PlanTask | None = None
        if "task_date" in normalized_updates or "task_time" in normalized_updates:
            task_before_update = await self.repository.get_task_for_owner(
                task_id=task_id,
                owner_user_id=owner_user_id,
            )
            if task_before_update is None:
                raise ApiError(code="not_found", message="Plan task not found.", status=404)
            task_date_before_update = task_before_update.task_date
            next_task_date = normalized_updates.get("task_date", task_date_before_update)
            await self._lock_task_schedule_dates(
                owner_user_id=owner_user_id,
                task_dates=[value for value in (task_date_before_update, next_task_date) if isinstance(value, date)],
            )
            locked_loader = getattr(self.repository, "get_task_for_owner_for_update", None)
            if callable(locked_loader):
                locked_task = await locked_loader(task_id=task_id, owner_user_id=owner_user_id)
                if locked_task is None:
                    raise ApiError(code="not_found", message="Plan task not found.", status=404)
                if locked_task.task_date != task_date_before_update:
                    raise ApiError(
                        code="milk_schedule_conflict",
                        message="The plan task schedule changed during update. Retry with the latest task.",
                        status=409,
                    )

        task = await self.repository.update_task(
            task_id=task_id,
            owner_user_id=owner_user_id,
            updates=normalized_updates,
        )
        if task is None:
            raise ApiError(code="not_found", message="Plan task not found.", status=404)
        await self._audit(
            owner_user_id=owner_user_id,
            action="plans.tasks.update",
            resource_type="plan_task",
            resource_id=str(task_id),
            request_id=request_id,
        )
        return task

    async def _lock_task_schedule_dates(self, *, owner_user_id: UUID, task_dates: list[date]) -> None:
        if not task_dates:
            return
        lock_dates = getattr(self.repository, "lock_milk_schedule_dates", None)
        if callable(lock_dates):
            await lock_dates(owner_user_id=owner_user_id, task_dates=task_dates)

    async def delete_task(self, *, owner_user_id: UUID, task_id: UUID, request_id: str = "") -> None:
        deleted = await self.repository.soft_delete_task(task_id=task_id, owner_user_id=owner_user_id, deleted_at=_utcnow())
        if deleted is None:
            raise ApiError(code="not_found", message="Plan task not found.", status=404)
        await self._audit(
            owner_user_id=owner_user_id,
            action="plans.tasks.delete",
            resource_type="plan_task",
            resource_id=str(task_id),
            request_id=request_id,
        )

    async def _lock_linked_pregnancy_plan(
        self,
        *,
        owner_user_id: UUID,
        task: PlanTask,
    ) -> Plan | None:
        if not _is_pregnancy_plan_task(task):
            return None
        if task.plan_id is None:
            raise ApiError(
                code="plan_task_sync_conflict",
                message="Pregnancy plan task is missing its parent plan.",
                status=409,
            )
        plan = await self.repository.get_plan_for_owner_for_update(
            plan_id=task.plan_id,
            owner_user_id=owner_user_id,
        )
        if plan is None:
            raise ApiError(code="not_found", message="Linked pregnancy plan not found.", status=404)
        if plan.plan_type != "pregnancy":
            raise ApiError(
                code="plan_task_sync_conflict",
                message="Plan task is linked to an incompatible plan type.",
                status=409,
            )
        return plan

    async def _sync_pregnancy_todo_from_task(
        self,
        *,
        owner_user_id: UUID,
        plan: Plan,
        task: PlanTask,
        state: str,
    ) -> None:
        item_id = _nonempty_text(task.payload.get("plan_todo_item_id"))
        period_id = _nonempty_text(task.payload.get("plan_todo_period_id"))
        if not item_id:
            raise ApiError(
                code="plan_task_sync_conflict",
                message="Pregnancy plan task is missing its todo item reference.",
                status=409,
            )
        next_payload = deepcopy(plan.payload)
        item = _find_todo_item_by_reference(
            next_payload,
            item_id=item_id,
            period_id=period_id or None,
        )
        if item is None:
            raise ApiError(
                code="plan_task_sync_conflict",
                message="Linked pregnancy plan todo item was not found.",
                status=409,
            )
        item["completed"] = state == "completed"
        item["status"] = state
        updated = await self.repository.update_plan_payload_and_version(
            plan_id=plan.id,
            owner_user_id=owner_user_id,
            expected_version=plan.version,
            payload=next_payload,
        )
        if updated is None:
            raise ApiError(
                code="version_conflict",
                message="Pregnancy plan changed during task update.",
                status=409,
            )

    async def _reserve_idempotency(
        self, *, owner_user_id: UUID, scope: str, key: str | None, payload: dict[str, Any]
    ) -> IdempotencyKey | None:
        if not key:
            return None
        if self.idempotency_service is None:
            raise ApiError(code="internal_error", message="Idempotency service is not configured.", status=500)
        decision = await self.idempotency_service.reserve(
            actor_user_id=owner_user_id,
            scope=scope,
            key=key,
            request_hash=request_hash(payload),
            expires_at=_utcnow() + timedelta(hours=24),
        )
        if decision.status == "replay" and not decision.record.response_ref:
            raise ApiError(code="idempotency_in_progress", message="Request is still in progress.", status=409)
        return decision.record

    async def _complete_idempotency(self, *, idempotency_record: IdempotencyKey | None, response_ref: str) -> None:
        if idempotency_record is not None and self.idempotency_service is not None:
            await self.idempotency_service.mark_completed(record=idempotency_record, response_ref=response_ref)

    async def _replay_plan(self, *, owner_user_id: UUID, response_ref: str) -> Plan:
        plan_id = parse_idempotency_response_ref(response_ref)
        plan = await self.repository.get_plan_for_owner(plan_id=plan_id, owner_user_id=owner_user_id)
        if plan is None:
            raise ApiError(code="conflict", message="Idempotency response resource is unavailable.", status=409)
        return plan

    async def _replay_task(self, *, owner_user_id: UUID, response_ref: str) -> PlanTask:
        task_id = parse_idempotency_response_ref(response_ref)
        task = await self.repository.get_task_for_owner(task_id=task_id, owner_user_id=owner_user_id)
        if task is None:
            raise ApiError(code="conflict", message="Idempotency response resource is unavailable.", status=409)
        return task

    async def _audit(
        self,
        *,
        owner_user_id: UUID,
        action: str,
        resource_type: str,
        resource_id: str,
        request_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                request_id=request_id,
                details=details,
            )

    def _validate_limit(self, limit: int) -> None:
        if limit < 1 or limit > 100:
            raise ApiError(code="validation_failed", message="limit must be between 1 and 100.", status=422)


def _milk_schedule_has_target_conflict(
    *,
    tasks_to_move: list[tuple[PlanTask, date, str]],
    locked_tasks: list[PlanTask],
) -> bool:
    proposed = {task.id: (new_date, new_time) for task, new_date, new_time in tasks_to_move}
    scheduled: dict[UUID, tuple[date, int, int]] = {}
    for task in locked_tasks:
        if task.status != "pending":
            continue
        task_date, task_time = proposed.get(task.id, (task.task_date, task.task_time))
        if task_date is None:
            continue
        start = _task_time_minutes(task_time)
        if start is None:
            continue
        scheduled[task.id] = (task_date, start, start + _task_duration_minutes(task))

    for task_id in proposed:
        moving = scheduled.get(task_id)
        if moving is None:
            continue
        moving_date, moving_start, moving_end = moving
        for other_id, (other_date, other_start, other_end) in scheduled.items():
            if other_id == task_id or other_date != moving_date:
                continue
            if moving_start < other_end and moving_end > other_start:
                return True
    return False


def _task_duration_minutes(task: PlanTask) -> int:
    payload = task.payload if isinstance(task.payload, dict) else {}
    try:
        return max(1, min(int(payload.get("duration_minutes") or 30), 240))
    except (TypeError, ValueError):
        return 30


def _task_time_minutes(value: Any) -> int | None:
    token = str(value or "").strip()
    if len(token) != 5 or token[2] != ":":
        return None
    try:
        hour, minute = int(token[:2]), int(token[3:])
    except ValueError:
        return None
    if hour not in range(24) or minute not in range(60):
        return None
    return hour * 60 + minute


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _required_uuid_value(value: Any, *, code: str) -> UUID:
    try:
        return UUID(str(value or ""))
    except ValueError as exc:
        raise ApiError(code=code, message="A valid UUID is required.", status=422) from exc


def _required_date_value(value: Any, *, code: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError as exc:
        raise ApiError(code=code, message="A valid date is required.", status=422) from exc


def _required_time_value(value: Any, *, code: str) -> str:
    token = str(value or "").strip()
    try:
        hour, minute = (int(part) for part in token.split(":"))
    except (TypeError, ValueError):
        hour, minute = -1, -1
    if len(token) != 5 or hour not in range(24) or minute not in range(60):
        raise ApiError(code=code, message="A valid HH:mm time is required.", status=422)
    return token


def _plan_effective_dates(payload: dict[str, Any]) -> tuple[date | None, date | None]:
    starts_on = _payload_date(payload.get("start_date"))
    ends_on = _payload_date(payload.get("end_date"))
    if ends_on is None and starts_on is not None:
        days = payload.get("days")
        if (
            isinstance(days, int)
            and not isinstance(days, bool)
            and 1 <= days <= 3660
        ):
            ends_on = starts_on + timedelta(days=days - 1)
    if starts_on is not None and ends_on is not None and ends_on < starts_on:
        ends_on = None
    return starts_on, ends_on


def _payload_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value or ""))
    except ValueError:
        return None


def _normalize_plan_payload(*, plan_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if plan_type.strip().lower() != "pregnancy":
        return deepcopy(payload)
    return normalize_pregnancy_plan_payload(payload)


def _find_todo_item_by_item_id(payload: dict[str, Any], item_id: str) -> dict[str, Any] | None:
    return _find_todo_item_by_reference(payload, item_id=item_id)


def _find_todo_item_by_reference(
    payload: dict[str, Any],
    *,
    item_id: str,
    period_id: str | None = None,
) -> dict[str, Any] | None:
    for period_index, period in enumerate(_todo_periods(payload)):
        current_period_id = _nonempty_text(period.get("id")) or f"period_{period_index + 1:02d}"
        if period_id is not None and current_period_id != period_id:
            continue
        items = period.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and _nonempty_text(item.get("item_id")) == item_id:
                return item
    return None


def _is_pregnancy_plan_task(task: PlanTask) -> bool:
    return _nonempty_text(task.payload.get("source")) == "pregnancy_plan"


def _task_links_to_pregnancy_todo(task: PlanTask, *, item_id: str) -> bool:
    return (
        _is_pregnancy_plan_task(task)
        and _nonempty_text(task.payload.get("plan_todo_item_id")) == item_id
    )


def _todo_periods(payload: dict[str, Any]) -> list[dict[str, Any]]:
    card = payload.get("card")
    if not isinstance(card, dict):
        return []
    card_json = card.get("card_json")
    if not isinstance(card_json, dict):
        return []
    todo_plan = card_json.get("todo_plan")
    if not isinstance(todo_plan, dict):
        return []
    periods = todo_plan.get("periods")
    if not isinstance(periods, list):
        return []
    return [period for period in periods if isinstance(period, dict)]


def _nonempty_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
