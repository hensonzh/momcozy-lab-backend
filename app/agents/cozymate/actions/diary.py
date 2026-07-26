from __future__ import annotations

from datetime import date
from typing import Any

from app.agent_runtime.actions import AgentActionApplyResult, AgentApplicationEvent, PermanentActionError
from app.agent_runtime.runs.models import AgentAction
from app.core.errors import ApiError
from app.modules.diary.events import DIARY_CHANGED_EVENT, diary_changed_payload
from app.modules.diary.models import DiaryEntry
from app.modules.diary.service import DiaryService


DIARY_SAVE_ACTION = "diary.entry.save"
DIARY_DELETE_ACTION = "diary.entry.delete"


class DiarySaveActionHandler:
    def __init__(self, *, service: DiaryService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        operation = _text(payload, "operation")
        if operation not in {"create", "update"}:
            raise PermanentActionError("invalid_diary_save_operation")
        entry_date = _entry_date(payload)
        content = _text(payload, "content")
        if not content:
            raise PermanentActionError("missing_diary_content")

        try:
            if operation == "create":
                entry = await self.service.create_entry(
                    owner_user_id=action.actor_user_id,
                    entry_date=entry_date,
                    values={"content": content},
                    request_id=f"agent-action:{action.id}",
                )
                changed = True
                applied_operation = "created"
            else:
                mutation = await self.service.update_entry_with_status(
                    owner_user_id=action.actor_user_id,
                    entry_date=entry_date,
                    values={"content": content},
                    request_id=f"agent-action:{action.id}",
                )
                entry = mutation.entry
                changed = mutation.changed
                applied_operation = "updated"
        except ApiError as exc:
            raise PermanentActionError(exc.code) from exc

        return _result(entry=entry, operation=applied_operation, changed=changed)


class DiaryDeleteActionHandler:
    def __init__(self, *, service: DiaryService) -> None:
        self.service = service

    async def __call__(self, action: AgentAction) -> AgentActionApplyResult:
        payload = dict(action.apply_payload or {})
        entry_date = _entry_date(payload)
        try:
            entry = await self.service.delete_entry(
                owner_user_id=action.actor_user_id,
                entry_date=entry_date,
                request_id=f"agent-action:{action.id}",
            )
        except ApiError as exc:
            raise PermanentActionError(exc.code) from exc
        return _result(entry=entry, operation="deleted", changed=True)


def _result(*, entry: DiaryEntry, operation: str, changed: bool) -> AgentActionApplyResult:
    events: tuple[AgentApplicationEvent, ...] = ()
    if changed:
        events = (
            AgentApplicationEvent(
                event_type=DIARY_CHANGED_EVENT,
                payload=diary_changed_payload(
                    entry=entry,
                    operation=operation,
                    source="agent_action",
                ),
            ),
        )
    return AgentActionApplyResult(
        resource_type="diary_entry",
        resource_id=str(entry.id),
        details={
            "operation": operation,
            "changed": changed,
        },
        application_events=events,
    )


def _entry_date(payload: dict[str, Any]) -> date:
    raw = _text(payload, "entry_date")
    if not raw:
        raise PermanentActionError("missing_diary_entry_date")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise PermanentActionError("invalid_diary_entry_date") from exc


def _text(payload: dict[str, Any], key: str) -> str:
    return str(payload.get(key) or "").strip()
