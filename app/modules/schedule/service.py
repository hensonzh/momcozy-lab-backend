from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime

from ...core.errors import ApiError
from ..audit.service import AuditService, IdempotencyService, request_hash
from ..plans.models import PlanTask
from .repository import ScheduleRepository
from .schemas import PersonalScheduleRead, PersonalScheduleUpdate, PersonalScheduleWrite, SchedulePageRead


class ScheduleService:
    def __init__(self, repository: ScheduleRepository, *, audit: AuditService | None = None,
                 idempotency: IdempotencyService | None = None) -> None:
        self.repository, self.audit, self.idempotency = repository, audit, idempotency

    async def create(self, owner: UUID, body: PersonalScheduleWrite, key: str, request_id: str) -> PersonalScheduleRead:
        if self.idempotency is not None:
            decision = await self.idempotency.reserve(actor_user_id=owner, scope="schedule.personal.create", key=key,
                request_hash=request_hash(body.model_dump(mode="json")), expires_at=datetime.now(timezone.utc) + timedelta(days=1))
            if decision.status == "replay":
                existing = await self.repository.personal_for_update(owner, UUID(decision.record.response_ref))
                if existing is None or existing.deleted_at is not None:
                    raise ApiError(code="not_found", message="Schedule item not found.", status=404)
                return PersonalScheduleRead.model_validate(_read_values(existing))
        task = PlanTask(owner_user_id=owner, task_date=body.date, task_time=body.start_time, title=body.title,
                        description=body.note, payload={"schedule_kind": "personal"})
        self.repository.session.add(task)
        await self.repository.flush()
        if self.idempotency is not None:
            await self.idempotency.mark_completed(record=decision.record, response_ref=str(task.id))
        await self._audit(owner, task.id, "created", request_id)
        return PersonalScheduleRead.model_validate(_read_values(task))

    async def update(self, owner: UUID, task_id: UUID, body: PersonalScheduleUpdate, request_id: str) -> PersonalScheduleRead:
        task = await self.repository.personal_for_update(owner, task_id)
        if task is None or task.deleted_at is not None:
            raise ApiError(code="not_found", message="Schedule item not found.", status=404)
        expected = body.expected_updated_at
        actual = _aware(task.updated_at)
        if actual != expected:
            if (task.title, task.task_date, task.task_time, task.description) == (body.title, body.date, body.start_time, body.note):
                return PersonalScheduleRead.model_validate(_read_values(task))
            raise ApiError(code="version_conflict", message="The schedule item changed. Refresh before editing.", status=409)
        task.title, task.task_date, task.task_time, task.description = body.title, body.date, body.start_time, body.note
        task.payload = {"schedule_kind": "personal"}
        task.updated_at = datetime.now(timezone.utc)
        await self.repository.flush()
        await self._audit(owner, task.id, "updated", request_id)
        return PersonalScheduleRead.model_validate(_read_values(task))

    async def delete(self, owner: UUID, task_id: UUID, expected_updated_at: AwareDatetime, request_id: str) -> None:
        task = await self.repository.personal_for_update(owner, task_id)
        if task is None:
            raise ApiError(code="not_found", message="Schedule item not found.", status=404)
        if task.deleted_at is not None:
            return
        if _aware(task.updated_at) != expected_updated_at:
            raise ApiError(code="version_conflict", message="The schedule item changed. Refresh before deleting.", status=409)
        task.deleted_at = datetime.now(timezone.utc)
        await self.repository.flush()
        await self._audit(owner, task.id, "deleted", request_id)

    async def read(self, owner: UUID, start: date, end: date, timezone_name: str, offset: int, limit: int) -> SchedulePageRead:
        if end <= start or (end - start).days > 62:
            raise ApiError(code="invalid_date_range", message="Schedule ranges must cover one to sixty-two days.", status=422)
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ApiError(code="invalid_timezone", message="Use an IANA timezone.", status=422) from exc
        batch = offset + limit + 1
        personal = await self.repository.personal(owner, start, end, batch)
        return SchedulePageRead(
            personal=[PersonalScheduleRead.model_validate(_read_values(item)) for item in personal[offset:offset + limit]],
            server_time=datetime.now(timezone.utc), has_more=len(personal) > offset + limit,
        )

    async def _audit(self, owner: UUID, task_id: UUID, action: str, request_id: str) -> None:
        if self.audit is not None:
            await self.audit.record(actor_user_id=owner, action=f"schedule.personal.{action}", resource_type="plan_task", resource_id=str(task_id), request_id=request_id, details={})


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _read_values(task: PlanTask) -> dict[str, object]:
    return {"id": task.id, "title": task.title, "date": task.task_date, "start_time": task.task_time, "note": task.description, "updated_at": _aware(task.updated_at)}
