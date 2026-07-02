from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from ...core.errors import ApiError
from ..audit import AuditService, IdempotencyKey, IdempotencyService, parse_idempotency_response_ref, request_hash
from .models import Plan, PlanTask
from .repository import PlansRepository


PLAN_CREATE_IDEMPOTENCY_SCOPE = "plans.create"
PLAN_TASK_CREATE_IDEMPOTENCY_SCOPE = "plans.tasks.create"


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
        idempotency_record = await self._reserve_idempotency(
            owner_user_id=owner_user_id,
            scope=PLAN_CREATE_IDEMPOTENCY_SCOPE,
            key=idempotency_key,
            payload={"plan_type": plan_type, "title": title, "summary": summary, "source": source, "payload": payload or {}},
        )
        if idempotency_record is not None and idempotency_record.response_ref:
            return await self._replay_plan(owner_user_id=owner_user_id, response_ref=idempotency_record.response_ref)

        plan = await self.repository.create_plan(
            owner_user_id=owner_user_id,
            plan_type=plan_type,
            title=title.strip(),
            summary=summary,
            source=source,
            payload=payload or {},
        )
        await self._complete_idempotency(idempotency_record=idempotency_record, response_ref=str(plan.id))
        await self._audit(owner_user_id=owner_user_id, action="plans.create", resource_type="plan", resource_id=str(plan.id), request_id=request_id)
        return plan

    async def list_plans(self, *, owner_user_id: UUID, status: str = "active", limit: int = 50) -> list[Plan]:
        self._validate_limit(limit)
        return await self.repository.list_plans(owner_user_id=owner_user_id, status=status, limit=limit)

    async def get_plan(self, *, owner_user_id: UUID, plan_id: UUID) -> Plan:
        plan = await self.repository.get_plan_for_owner(plan_id=plan_id, owner_user_id=owner_user_id)
        if plan is None:
            raise ApiError(code="not_found", message="Plan not found.", status=404)
        return plan

    async def delete_plan(self, *, owner_user_id: UUID, plan_id: UUID, request_id: str = "") -> None:
        deleted = await self.repository.soft_delete_plan(plan_id=plan_id, owner_user_id=owner_user_id, deleted_at=_utcnow())
        if deleted is None:
            raise ApiError(code="not_found", message="Plan not found.", status=404)
        await self._audit(owner_user_id=owner_user_id, action="plans.delete", resource_type="plan", resource_id=str(plan_id), request_id=request_id)

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
            payload={"plan_id": str(plan_id or ""), "task_date": str(task_date or ""), "task_time": task_time, "title": title},
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
        await self._audit(owner_user_id=owner_user_id, action="plans.tasks.create", resource_type="plan_task", resource_id=str(task.id), request_id=request_id)
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

    async def set_task_completed(self, *, owner_user_id: UUID, task_id: UUID, completed: bool, request_id: str = "") -> PlanTask:
        task = await self.repository.set_task_completed(
            task_id=task_id,
            owner_user_id=owner_user_id,
            completed=completed,
            completed_at=_utcnow() if completed else None,
        )
        if task is None:
            raise ApiError(code="not_found", message="Plan task not found.", status=404)
        await self._audit(owner_user_id=owner_user_id, action="plans.tasks.complete", resource_type="plan_task", resource_id=str(task_id), request_id=request_id)
        return task

    async def delete_task(self, *, owner_user_id: UUID, task_id: UUID, request_id: str = "") -> None:
        deleted = await self.repository.soft_delete_task(task_id=task_id, owner_user_id=owner_user_id, deleted_at=_utcnow())
        if deleted is None:
            raise ApiError(code="not_found", message="Plan task not found.", status=404)
        await self._audit(owner_user_id=owner_user_id, action="plans.tasks.delete", resource_type="plan_task", resource_id=str(task_id), request_id=request_id)

    async def _reserve_idempotency(self, *, owner_user_id: UUID, scope: str, key: str | None, payload: dict[str, Any]) -> IdempotencyKey | None:
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
