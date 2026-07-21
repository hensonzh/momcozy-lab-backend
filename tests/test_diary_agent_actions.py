import asyncio
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from app.agent_runtime.runs.models import AgentAction
from app.agents.cozymate.actions.diary import (
    PREGNANCY_DIARY_DELETE_ACTION,
    PREGNANCY_DIARY_SAVE_ACTION,
    PregnancyDiaryDeleteActionHandler,
    PregnancyDiarySaveActionHandler,
)
from app.modules.diary.models import PregnancyDiaryEntry
from app.modules.diary.repository import DiaryEntryMutation
from app.agent_runtime.actions.errors import PermanentActionError


def test_pregnancy_diary_save_action_creates_entry_and_change_event() -> None:
    service = FakeDiaryService()
    action = _action(
        action_type=PREGNANCY_DIARY_SAVE_ACTION,
        payload={"operation": "create", "entry_date": "2026-07-21", "content": "Today felt calm."},
    )

    result = asyncio.run(PregnancyDiarySaveActionHandler(service=service)(action))

    assert service.create_kwargs["owner_user_id"] == action.actor_user_id
    assert service.create_kwargs["values"] == {"content": "Today felt calm."}
    assert service.create_kwargs["request_id"] == f"agent-action:{action.id}"
    assert result.resource_id == str(service.entry.id)
    assert result.details == {"operation": "created", "changed": True}
    assert result.application_events[0].event_type == "pregnancy_diary.changed"
    assert result.application_events[0].payload["source"] == "agent_action"


def test_pregnancy_diary_save_action_updates_complete_entry() -> None:
    service = FakeDiaryService()
    action = _action(
        action_type=PREGNANCY_DIARY_SAVE_ACTION,
        payload={"operation": "update", "entry_date": "2026-07-21", "content": "Complete rewritten entry."},
    )

    result = asyncio.run(PregnancyDiarySaveActionHandler(service=service)(action))

    assert service.update_kwargs["values"] == {"content": "Complete rewritten entry."}
    assert result.details == {"operation": "updated", "changed": True}


def test_pregnancy_diary_delete_action_soft_deletes_entry() -> None:
    service = FakeDiaryService()
    action = _action(
        action_type=PREGNANCY_DIARY_DELETE_ACTION,
        payload={"entry_date": "2026-07-21"},
    )

    result = asyncio.run(PregnancyDiaryDeleteActionHandler(service=service)(action))

    assert service.delete_kwargs["owner_user_id"] == action.actor_user_id
    assert result.details == {"operation": "deleted", "changed": True}
    assert result.application_events[0].payload["operation"] == "deleted"


def test_pregnancy_diary_action_rejects_invalid_operation() -> None:
    action = _action(
        action_type=PREGNANCY_DIARY_SAVE_ACTION,
        payload={"operation": "append", "entry_date": "2026-07-21", "content": "No."},
    )

    with pytest.raises(PermanentActionError) as exc_info:
        asyncio.run(PregnancyDiarySaveActionHandler(service=FakeDiaryService())(action))

    assert exc_info.value.code == "invalid_diary_save_operation"


class FakeDiaryService:
    def __init__(self) -> None:
        self.entry = PregnancyDiaryEntry(
            id=uuid4(),
            owner_user_id=uuid4(),
            entry_date=date(2026, 7, 21),
            content="",
            updated_at=datetime(2026, 7, 21, tzinfo=timezone.utc),
        )
        self.create_kwargs: dict = {}
        self.update_kwargs: dict = {}
        self.delete_kwargs: dict = {}

    async def create_entry(self, **kwargs):
        self.create_kwargs = kwargs
        self.entry.owner_user_id = kwargs["owner_user_id"]
        self.entry.entry_date = kwargs["entry_date"]
        self.entry.content = kwargs["values"]["content"]
        return self.entry

    async def update_entry_with_status(self, **kwargs):
        self.update_kwargs = kwargs
        self.entry.owner_user_id = kwargs["owner_user_id"]
        self.entry.entry_date = kwargs["entry_date"]
        self.entry.content = kwargs["values"]["content"]
        return DiaryEntryMutation(entry=self.entry, changed=True)

    async def delete_entry(self, **kwargs):
        self.delete_kwargs = kwargs
        self.entry.owner_user_id = kwargs["owner_user_id"]
        self.entry.entry_date = kwargs["entry_date"]
        return self.entry


def _action(*, action_type: str, payload: dict) -> AgentAction:
    return AgentAction(
        id=uuid4(),
        run_id=uuid4(),
        actor_user_id=uuid4(),
        action_type=action_type,
        target_type="pregnancy_diary_entry",
        target_id="",
        status="confirmed",
        side_effect_level="low" if action_type == PREGNANCY_DIARY_SAVE_ACTION else "medium",
        preview_payload={},
        apply_payload=payload,
        idempotency_key="diary-action",
        error_code="",
    )
