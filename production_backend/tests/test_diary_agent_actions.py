import asyncio
from datetime import date
from uuid import uuid4

import pytest

from production_backend.app.modules.agent_runtime.models import AgentAction
from production_backend.app.modules.diary.agent_actions import (
    PREGNANCY_DIARY_ENTRY_CREATE_ACTION,
    PREGNANCY_DIARY_ENTRY_DELETE_ACTION,
    PREGNANCY_DIARY_ENTRY_UPDATE_ACTION,
    PregnancyDiaryEntryCreateActionHandler,
    PregnancyDiaryEntryDeleteActionHandler,
    PregnancyDiaryEntryUpdateActionHandler,
)
from production_backend.app.modules.diary.models import PregnancyDiaryEntry
from production_backend.app.workers.errors import PermanentJobError


def test_pregnancy_diary_create_action_calls_create_service() -> None:
    service = FakeDiaryService()
    action = _action(
        action_type=PREGNANCY_DIARY_ENTRY_CREATE_ACTION,
        apply_payload={
            "entry_date": "2026-07-04",
            "values": {"mood": "calm", "content": "Today I felt steady.", "unknown_field": "ignored"},
        },
    )

    result = asyncio.run(PregnancyDiaryEntryCreateActionHandler(service=service)(action))

    assert result.resource_type == "pregnancy_diary_entry"
    assert result.resource_id == str(service.entry.id)
    assert service.create_kwargs["entry_date"] == date(2026, 7, 4)
    assert service.create_kwargs["values"] == {"mood": "calm", "content": "Today I felt steady."}


def test_pregnancy_diary_update_action_calls_update_service() -> None:
    service = FakeDiaryService()
    action = _action(
        action_type=PREGNANCY_DIARY_ENTRY_UPDATE_ACTION,
        apply_payload={"entry_date": "2026-07-04", "values": {"content": "Updated entry"}},
    )

    asyncio.run(PregnancyDiaryEntryUpdateActionHandler(service=service)(action))

    assert service.update_kwargs["entry_date"] == date(2026, 7, 4)
    assert service.update_kwargs["values"] == {"content": "Updated entry"}


def test_pregnancy_diary_delete_action_calls_delete_service() -> None:
    service = FakeDiaryService()
    action = _action(
        action_type=PREGNANCY_DIARY_ENTRY_DELETE_ACTION,
        apply_payload={"entry_date": "2026-07-04"},
    )

    asyncio.run(PregnancyDiaryEntryDeleteActionHandler(service=service)(action))

    assert service.delete_kwargs["entry_date"] == date(2026, 7, 4)


@pytest.mark.parametrize(
    "handler",
    [
        PregnancyDiaryEntryCreateActionHandler,
        PregnancyDiaryEntryUpdateActionHandler,
        PregnancyDiaryEntryDeleteActionHandler,
    ],
)
def test_pregnancy_diary_actions_require_entry_date(handler) -> None:
    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(handler(service=FakeDiaryService())(_action(action_type="test", apply_payload={})))

    assert exc_info.value.code == "missing_entry_date"


class FakeDiaryService:
    def __init__(self) -> None:
        self.entry = PregnancyDiaryEntry(
            id=uuid4(),
            owner_user_id=uuid4(),
            entry_date=date(2026, 7, 4),
            mood="calm",
            content="Today I felt steady.",
            symptom_tags=[],
            attachments=[],
        )
        self.create_kwargs = {}
        self.update_kwargs = {}
        self.delete_kwargs = {}

    async def create_entry(self, **kwargs):
        self.create_kwargs = kwargs
        return self.entry

    async def update_entry(self, **kwargs):
        self.update_kwargs = kwargs
        return self.entry

    async def delete_entry(self, **kwargs):
        self.delete_kwargs = kwargs
        return self.entry


def _action(*, action_type: str, apply_payload: dict) -> AgentAction:
    return AgentAction(
        id=uuid4(),
        run_id=uuid4(),
        actor_user_id=uuid4(),
        action_type=action_type,
        target_type="pregnancy_diary_entry",
        target_id="2026-07-04",
        status="confirmed",
        side_effect_level="low",
        preview_payload={},
        apply_payload=apply_payload,
        idempotency_key="idem-action",
        error_code="",
    )
