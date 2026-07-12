from __future__ import annotations

from datetime import date
from typing import Any

from ...core.errors import ApiError
from ...workers.errors import PermanentJobError
from ..agent_runtime.actions.executor import AgentActionApplyResult, AgentApplicationEvent
from ..agent_runtime.models import AgentAction
from .service import DiaryService
from .events import PREGNANCY_DIARY_CHANGED_EVENT, pregnancy_diary_changed_payload


PREGNANCY_DIARY_ENTRY_DELETE_ACTION = "pregnancy_diary.entry.delete"


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
        application_events=(
            AgentApplicationEvent(
                event_type=PREGNANCY_DIARY_CHANGED_EVENT,
                payload=pregnancy_diary_changed_payload(entry=entry, operation="deleted", source="agent_action"),
            ),
        ),
    )


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
