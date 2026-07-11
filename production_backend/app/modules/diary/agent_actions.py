from __future__ import annotations

from datetime import date
from typing import Any

from ...core.errors import ApiError
from ...workers.errors import PermanentJobError
from ..agent_runtime.actions.outbox import AgentActionApplyResult
from ..agent_runtime.models import AgentAction
from .service import DiaryService


PREGNANCY_DIARY_ENTRY_CREATE_ACTION = "pregnancy_diary.entry.create"
PREGNANCY_DIARY_ENTRY_UPDATE_ACTION = "pregnancy_diary.entry.update"
PREGNANCY_DIARY_ENTRY_DELETE_ACTION = "pregnancy_diary.entry.delete"

_DIARY_VALUE_FIELDS = frozenset(
    {
        "gestational_week",
        "mood",
        "energy_level",
        "sleep_summary",
        "fetal_movement",
        "symptom_tags",
        "appointment_note",
        "nutrition_note",
        "content",
        "attachments",
    }
)


class PregnancyDiaryEntryCreateActionHandler:
    def __init__(self, *, service: DiaryService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        entry_date = _required_date(payload, "entry_date", "missing_entry_date", "invalid_entry_date")
        values = _diary_values(payload)
        if not values:
            raise PermanentJobError("missing_diary_values")

        try:
            entry = await self.service.create_entry(
                owner_user_id=action.actor_user_id,
                entry_date=entry_date,
                values=values,
                request_id=f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc

        return _action_result(action=action, entry=entry)


class PregnancyDiaryEntryUpdateActionHandler:
    def __init__(self, *, service: DiaryService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        entry_date = _required_date(payload, "entry_date", "missing_entry_date", "invalid_entry_date")
        values = _diary_values(payload)
        if not values:
            raise PermanentJobError("missing_diary_values")

        try:
            entry = await self.service.update_entry(
                owner_user_id=action.actor_user_id,
                entry_date=entry_date,
                values=values,
                request_id=f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc

        return _action_result(action=action, entry=entry)


class PregnancyDiaryEntryDeleteActionHandler:
    def __init__(self, *, service: DiaryService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        entry_date = _required_date(payload, "entry_date", "missing_entry_date", "invalid_entry_date")

        try:
            entry = await self.service.delete_entry(
                owner_user_id=action.actor_user_id,
                entry_date=entry_date,
                request_id=f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentJobError(exc.code) from exc

        return _action_result(action=action, entry=entry)


def _action_result(*, action: AgentAction, entry: Any) -> AgentActionApplyResult:
    return AgentActionApplyResult(
        resource_type="pregnancy_diary_entry",
        resource_id=str(entry.id),
        details={
            "entry_date": entry.entry_date.isoformat(),
            "agent_action_id": str(action.id),
            "agent_run_id": str(action.run_id),
        },
    )


def _diary_values(payload: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    nested_values = payload.get("values")
    if isinstance(nested_values, dict):
        values.update({key: value for key, value in nested_values.items() if key in _DIARY_VALUE_FIELDS})
    values.update({key: payload[key] for key in _DIARY_VALUE_FIELDS if key in payload})
    return values


def _required_date(payload: dict[str, Any], key: str, missing_code: str, invalid_code: str) -> date:
    value = payload.get(key)
    if value in ("", None):
        raise PermanentJobError(missing_code)
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise PermanentJobError(invalid_code) from exc
