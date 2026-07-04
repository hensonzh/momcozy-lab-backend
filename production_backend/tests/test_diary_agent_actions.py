import asyncio
from datetime import date
from uuid import uuid4

import pytest

from production_backend.app.modules.agent_runtime.models import AgentAction
from production_backend.app.modules.diary.agent_actions import DIARY_ENTRY_UPSERT_ACTION, DiaryEntryUpsertActionHandler
from production_backend.app.modules.diary.models import PregnancyDiaryEntry
from production_backend.app.workers.errors import PermanentJobError


def test_diary_entry_upsert_action_handler_upserts_entry_through_service() -> None:
    service = FakeDiaryService()
    action = _action(
        apply_payload={
            "entry_date": "2026-07-04",
            "values": {
                "mood": "calm",
                "content": "Today I felt steady.",
                "unknown_field": "ignored",
            },
        }
    )

    result = asyncio.run(DiaryEntryUpsertActionHandler(service=service)(action))

    assert result.resource_type == "pregnancy_diary_entry"
    assert result.resource_id == str(service.entry.id)
    assert result.details == {
        "entry_date": "2026-07-04",
        "agent_action_id": str(action.id),
        "agent_run_id": str(action.run_id),
    }
    assert service.upsert_entry_kwargs["owner_user_id"] == action.actor_user_id
    assert service.upsert_entry_kwargs["entry_date"] == date(2026, 7, 4)
    assert service.upsert_entry_kwargs["values"] == {
        "mood": "calm",
        "content": "Today I felt steady.",
    }
    assert service.upsert_entry_kwargs["request_id"] == f"agent-action:{action.id}"


def test_diary_entry_upsert_action_handler_accepts_flat_payload_values() -> None:
    service = FakeDiaryService()
    action = _action(apply_payload={"entry_date": date(2026, 7, 4), "mood": "calm", "content": "Flat payload"})

    asyncio.run(DiaryEntryUpsertActionHandler(service=service)(action))

    assert service.upsert_entry_kwargs["values"] == {"mood": "calm", "content": "Flat payload"}


def test_diary_entry_upsert_action_handler_rejects_missing_entry_date() -> None:
    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(
            DiaryEntryUpsertActionHandler(service=FakeDiaryService())(
                _action(apply_payload={"values": {"content": "Today I felt steady."}})
            )
        )

    assert exc_info.value.code == "missing_entry_date"


def test_diary_entry_upsert_action_handler_rejects_missing_values() -> None:
    with pytest.raises(PermanentJobError) as exc_info:
        asyncio.run(
            DiaryEntryUpsertActionHandler(service=FakeDiaryService())(_action(apply_payload={"entry_date": "2026-07-04"}))
        )

    assert exc_info.value.code == "missing_diary_values"


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
        self.upsert_entry_kwargs = {}

    async def upsert_entry(self, **kwargs):
        self.upsert_entry_kwargs = kwargs
        self.entry.owner_user_id = kwargs["owner_user_id"]
        self.entry.entry_date = kwargs["entry_date"]
        for key, value in kwargs["values"].items():
            setattr(self.entry, key, value)
        return self.entry


def _action(*, apply_payload: dict) -> AgentAction:
    return AgentAction(
        id=uuid4(),
        run_id=uuid4(),
        actor_user_id=uuid4(),
        action_type=DIARY_ENTRY_UPSERT_ACTION,
        target_type="pregnancy_diary_entry",
        target_id="",
        status="confirmed",
        side_effect_level="medium",
        preview_payload={},
        apply_payload=apply_payload,
        idempotency_key="idem-action",
        error_code="",
    )
