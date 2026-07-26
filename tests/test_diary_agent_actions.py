import asyncio
from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from app.agent_runtime.runs.models import AgentAction
from app.agents.cozymate.actions.diary import (
    DIARY_DELETE_ACTION,
    DIARY_SAVE_ACTION,
    DiaryDeleteActionHandler,
    DiarySaveActionHandler,
)
from app.modules.diary.models import DiaryEntry
from app.modules.diary.repository import DiaryEntryMutation
from app.agent_runtime.actions.errors import PermanentActionError


def test_diary_save_action_creates_entry_and_change_event() -> None:
    service = FakeDiaryService()
    action = _action(
        action_type=DIARY_SAVE_ACTION,
        payload={
            "operation": "create",
            "entry_date": "2026-07-21",
            "content": "Today felt calm.",
        },
    )

    result = asyncio.run(DiarySaveActionHandler(service=service)(action))

    assert service.create_kwargs["owner_user_id"] == action.actor_user_id
    assert "diary_type" not in service.create_kwargs
    assert service.create_kwargs["values"] == {"content": "Today felt calm."}
    assert service.create_kwargs["request_id"] == f"agent-action:{action.id}"
    assert result.resource_id == str(service.entry.id)
    assert result.details == {
        "operation": "created",
        "changed": True,
    }
    assert result.application_events[0].event_type == "diary.changed"
    assert result.application_events[0].payload["source"] == "agent_action"


def test_diary_save_action_updates_complete_entry() -> None:
    service = FakeDiaryService()
    action = _action(
        action_type=DIARY_SAVE_ACTION,
        payload={
            "operation": "update",
            "entry_date": "2026-07-21",
            "content": "Complete rewritten entry.",
        },
    )

    result = asyncio.run(DiarySaveActionHandler(service=service)(action))

    assert service.update_kwargs["values"] == {"content": "Complete rewritten entry."}
    assert "diary_type" not in service.update_kwargs
    assert result.details == {
        "operation": "updated",
        "changed": True,
    }


def test_diary_delete_action_soft_deletes_entry() -> None:
    service = FakeDiaryService()
    action = _action(
        action_type=DIARY_DELETE_ACTION,
        payload={"entry_date": "2026-07-21"},
    )

    result = asyncio.run(DiaryDeleteActionHandler(service=service)(action))

    assert service.delete_kwargs["owner_user_id"] == action.actor_user_id
    assert "diary_type" not in service.delete_kwargs
    assert result.details == {
        "operation": "deleted",
        "changed": True,
    }
    assert result.application_events[0].payload["operation"] == "deleted"


def test_diary_action_rejects_invalid_operation() -> None:
    action = _action(
        action_type=DIARY_SAVE_ACTION,
        payload={"operation": "append", "entry_date": "2026-07-21", "content": "No."},
    )

    with pytest.raises(PermanentActionError) as exc_info:
        asyncio.run(DiarySaveActionHandler(service=FakeDiaryService())(action))

    assert exc_info.value.code == "invalid_diary_save_operation"


class FakeDiaryService:
    def __init__(self) -> None:
        self.entry = DiaryEntry(
            id=uuid4(),
            owner_user_id=uuid4(),
            entry_date=date(2026, 7, 21),
            content="",
            attributes={},
            attachments=[],
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
        target_type="diary_entry",
        target_id="",
        status="confirmed",
        side_effect_level="low" if action_type == DIARY_SAVE_ACTION else "medium",
        preview_payload={},
        apply_payload=payload,
        idempotency_key="diary-action",
        error_code="",
    )
