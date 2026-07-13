from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from ...core.errors import ApiError
from ..audit import AuditService, IdempotencyKey, IdempotencyService, parse_idempotency_response_ref, request_hash
from .models import Plan, PlanTask
from .repository import PlansRepository


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
        normalized_payload = _normalize_plan_payload(plan_type=plan_type, payload=payload or {})
        idempotency_record = await self._reserve_idempotency(
            owner_user_id=owner_user_id,
            scope=PLAN_CREATE_IDEMPOTENCY_SCOPE,
            key=idempotency_key,
            payload={"plan_type": plan_type, "title": title, "summary": summary, "source": source, "payload": normalized_payload},
        )
        if idempotency_record is not None and idempotency_record.response_ref:
            return await self._replay_plan(owner_user_id=owner_user_id, response_ref=idempotency_record.response_ref)

        plan = await self.repository.create_plan(
            owner_user_id=owner_user_id,
            plan_type=plan_type,
            title=title.strip(),
            summary=summary,
            source=source,
            payload=normalized_payload,
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
        limit: int = 50,
    ) -> list[Plan]:
        self._validate_limit(limit)
        return await self.repository.list_plans(
            owner_user_id=owner_user_id,
            plan_type=plan_type.strip(),
            status=status,
            limit=limit,
        )

    async def get_plan(self, *, owner_user_id: UUID, plan_id: UUID) -> Plan:
        plan = await self.repository.get_plan_for_owner(plan_id=plan_id, owner_user_id=owner_user_id)
        if plan is None:
            raise ApiError(code="not_found", message="Plan not found.", status=404)
        return plan

    async def delete_plan(self, *, owner_user_id: UUID, plan_id: UUID, request_id: str = "") -> None:
        deleted = await self.repository.soft_delete_plan(plan_id=plan_id, owner_user_id=owner_user_id, deleted_at=_utcnow())
        if deleted is None:
            raise ApiError(code="not_found", message="Plan not found.", status=404)
        await self._audit(
            owner_user_id=owner_user_id, action="plans.delete", resource_type="plan", resource_id=str(plan_id), request_id=request_id
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
        plan = await self.get_plan(owner_user_id=owner_user_id, plan_id=plan_id)
        if plan.plan_type != "milk_management":
            raise ApiError(code="validation_failed", message="Plan is not a milk-management plan.", status=422)
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
        task = await self.repository.set_task_completed(
            task_id=task_id,
            owner_user_id=owner_user_id,
            completed=completed,
            completed_at=_utcnow() if completed else None,
        )
        if task is None:
            raise ApiError(code="not_found", message="Plan task not found.", status=404)
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
        task = await self.repository.set_task_state(
            task_id=task_id,
            owner_user_id=owner_user_id,
            state=normalized_state,
            completed_at=_utcnow() if normalized_state == "completed" else None,
        )
        if task is None:
            raise ApiError(code="not_found", message="Plan task not found.", status=404)
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
        await self._complete_idempotency(idempotency_record=idempotency_record, response_ref=str(plan_id))
        await self._audit(
            owner_user_id=owner_user_id,
            action="plans.todos.completion",
            resource_type="plan",
            resource_id=str(plan_id),
            request_id=request_id,
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

    async def _audit(self, *, owner_user_id: UUID, action: str, resource_type: str, resource_id: str, request_id: str) -> None:
        if self.audit_service is not None:
            await self.audit_service.record(
                actor_user_id=owner_user_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                request_id=request_id,
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


def _normalize_plan_payload(*, plan_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(payload)
    if plan_type.strip().lower() != "pregnancy":
        return normalized
    periods = _todo_periods(normalized)
    for period_index, period in enumerate(periods):
        raw_items = period.get("items")
        if not isinstance(raw_items, list):
            continue
        normalized_items: list[Any] = []
        period_id = _nonempty_text(period.get("id")) or f"period-{period_index + 1}"
        for item_index, raw_item in enumerate(raw_items):
            if isinstance(raw_item, str):
                item: dict[str, Any] = {"title": raw_item}
            elif isinstance(raw_item, dict):
                item = deepcopy(raw_item)
            else:
                normalized_items.append(raw_item)
                continue
            stable_id = _nonempty_text(item.get("item_id")) or _nonempty_text(item.get("id"))
            if not stable_id:
                title = _nonempty_text(item.get("title"))
                seed = f"momcozy:pregnancy-plan:{period_id}:{item_index}:{title}"
                stable_id = f"todo-{uuid5(NAMESPACE_URL, seed)}"
            item["item_id"] = stable_id
            normalized_items.append(item)
        period["items"] = normalized_items
    return normalized


def _find_todo_item_by_item_id(payload: dict[str, Any], item_id: str) -> dict[str, Any] | None:
    for period in _todo_periods(payload):
        items = period.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and _nonempty_text(item.get("item_id")) == item_id:
                return item
    return None


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
