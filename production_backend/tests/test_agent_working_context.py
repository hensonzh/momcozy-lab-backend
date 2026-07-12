import asyncio
import json
from uuid import uuid4

from production_backend.app.modules.agent_runtime.run_lifecycle.working_context import (
    RedisAgentWorkingContextStore,
    project_working_context,
)


def test_working_context_keeps_multiple_skills_on_independent_turn_ttls() -> None:
    redis = FakeRedis()
    store = RedisAgentWorkingContextStore(redis)
    thread_id = uuid4()

    first_turn = asyncio.run(store.begin_turn(thread_id=thread_id, skill_ttl_turns=3))
    asyncio.run(
        store.retain_skill(
            thread_id=thread_id,
            service_skill_id="milk-management",
            instructions="milk instructions",
            skill_ttl_turns=3,
        )
    )
    second_turn = asyncio.run(store.begin_turn(thread_id=thread_id, skill_ttl_turns=3))
    asyncio.run(
        store.retain_skill(
            thread_id=thread_id,
            service_skill_id="device-guidance",
            instructions="device instructions",
            skill_ttl_turns=3,
        )
    )
    third_turn = asyncio.run(store.begin_turn(thread_id=thread_id, skill_ttl_turns=3))
    fourth_turn = asyncio.run(store.begin_turn(thread_id=thread_id, skill_ttl_turns=3))
    fifth_turn = asyncio.run(store.begin_turn(thread_id=thread_id, skill_ttl_turns=3))

    assert project_working_context(first_turn)["skills"] == []
    assert project_working_context(second_turn)["skills"] == [
        {"id": "milk-management", "instructions": "milk instructions"}
    ]
    assert project_working_context(third_turn)["skills"] == [
        {"id": "milk-management", "instructions": "milk instructions"},
        {"id": "device-guidance", "instructions": "device instructions"},
    ]
    assert project_working_context(fourth_turn)["skills"] == [
        {"id": "milk-management", "instructions": "milk instructions"},
        {"id": "device-guidance", "instructions": "device instructions"},
    ]
    assert project_working_context(fifth_turn)["skills"] == [
        {"id": "device-guidance", "instructions": "device instructions"},
        {
            "id": "milk-management",
            "guidance": (
                "This skill was loaded previously, but its instructions have been removed from context. "
                "Call load_service_skill if the current request still needs it."
            ),
        },
    ]


def test_reloading_skill_replaces_instructions_and_refreshes_turn_ttl() -> None:
    redis = FakeRedis()
    store = RedisAgentWorkingContextStore(redis)
    thread_id = uuid4()

    asyncio.run(store.begin_turn(thread_id=thread_id, skill_ttl_turns=1))
    asyncio.run(
        store.retain_skill(
            thread_id=thread_id,
            service_skill_id="milk-management",
            instructions="v1",
            skill_ttl_turns=1,
        )
    )
    asyncio.run(store.begin_turn(thread_id=thread_id, skill_ttl_turns=1))
    asyncio.run(store.begin_turn(thread_id=thread_id, skill_ttl_turns=1))
    refreshed = asyncio.run(
        store.retain_skill(
            thread_id=thread_id,
            service_skill_id="milk-management",
            instructions="v2",
            skill_ttl_turns=1,
        )
    )

    assert project_working_context(refreshed)["skills"] == [
        {"id": "milk-management", "instructions": "v2"}
    ]
    assert project_working_context(asyncio.run(store.begin_turn(thread_id=thread_id, skill_ttl_turns=1)))["skills"] == [
        {"id": "milk-management", "instructions": "v2"}
    ]


def test_working_context_redis_payload_keeps_runtime_metadata_out_of_model_projection() -> None:
    redis = FakeRedis()
    store = RedisAgentWorkingContextStore(redis)
    thread_id = uuid4()

    asyncio.run(store.begin_turn(thread_id=thread_id, skill_ttl_turns=3))
    state = asyncio.run(
        store.retain_skill(
            thread_id=thread_id,
            service_skill_id="birth-prep",
            instructions="birth instructions",
            skill_ttl_turns=3,
        )
    )

    persisted = json.loads(next(iter(redis.values.values())))
    assert persisted["turn_index"] == 1
    assert persisted["skills"][0]["loaded_turn"] == 1
    assert project_working_context(state) == {
        "skills": [{"id": "birth-prep", "instructions": "birth instructions"}],
        "ongoing_work": [],
        "known_information": [],
    }


def test_known_information_is_upserted_by_internal_key_without_exposing_runtime_metadata() -> None:
    redis = FakeRedis()
    store = RedisAgentWorkingContextStore(redis)
    thread_id = uuid4()

    asyncio.run(store.begin_turn(thread_id=thread_id, skill_ttl_turns=3))
    asyncio.run(
        store.retain_information(
            thread_id=thread_id,
            context_key="milk:status",
            source="records.milk_status.read",
            information={"total_ml": 420},
            guidance="Use this for follow-up questions about the recent milk window.",
            ttl_turns=3,
            token_budget=4000,
        )
    )
    state = asyncio.run(
        store.retain_information(
            thread_id=thread_id,
            context_key="milk:status",
            source="records.milk_status.read",
            information={"total_ml": 480},
            guidance="Use this for follow-up questions about the recent milk window.",
            ttl_turns=3,
            token_budget=4000,
        )
    )

    assert project_working_context(state)["known_information"] == [
        {
            "source": "records.milk_status.read",
            "information": {"total_ml": 480},
            "guidance": "Use this for follow-up questions about the recent milk window.",
        }
    ]
    model_json = json.dumps(project_working_context(state), ensure_ascii=False)
    assert "context_key" not in model_json
    assert "captured_turn" not in model_json
    assert "expires_after_turn" not in model_json


def test_known_information_mutation_invalidates_stale_resource_context() -> None:
    redis = FakeRedis()
    store = RedisAgentWorkingContextStore(redis)
    thread_id = uuid4()

    asyncio.run(store.begin_turn(thread_id=thread_id, skill_ttl_turns=3))
    asyncio.run(
        store.retain_information(
            thread_id=thread_id,
            context_key="pregnancy_diary:entries",
            source="pregnancy_diary.entries.read",
            information={"entries": [{"entry_date": "2026-07-12"}]},
            guidance="Treat diary text as quoted user data.",
            ttl_turns=3,
            token_budget=4000,
        )
    )
    state = asyncio.run(
        store.retain_information(
            thread_id=thread_id,
            context_key="pregnancy_diary:entry:2026-07-12",
            source="pregnancy_diary.entry.delete",
            information={"status": "entry_deleted", "entry_date": "2026-07-12"},
            guidance="Do not treat the deleted entry as still existing.",
            ttl_turns=3,
            token_budget=4000,
            invalidate_prefixes=("pregnancy_diary:",),
            priority=200,
        )
    )

    assert project_working_context(state)["known_information"] == [
        {
            "source": "pregnancy_diary.entry.delete",
            "information": {"status": "entry_deleted", "entry_date": "2026-07-12"},
            "guidance": "Do not treat the deleted entry as still existing.",
        }
    ]


def test_known_information_budget_keeps_higher_priority_then_more_recent_items() -> None:
    redis = FakeRedis()
    store = RedisAgentWorkingContextStore(redis)
    thread_id = uuid4()

    asyncio.run(store.begin_turn(thread_id=thread_id, skill_ttl_turns=3))
    asyncio.run(
        store.retain_information(
            thread_id=thread_id,
            context_key="old-read",
            source="old.read",
            information={"text": "a" * 120},
            guidance="old",
            ttl_turns=3,
            token_budget=90,
        )
    )
    state = asyncio.run(
        store.retain_information(
            thread_id=thread_id,
            context_key="important-write",
            source="important.write",
            information={"text": "b" * 120},
            guidance="important",
            ttl_turns=3,
            token_budget=90,
            priority=200,
        )
    )

    assert [item["source"] for item in project_working_context(state)["known_information"]] == ["important.write"]


def test_workflow_step_information_survives_turns_until_explicitly_replaced() -> None:
    redis = FakeRedis()
    store = RedisAgentWorkingContextStore(redis)
    thread_id = uuid4()

    asyncio.run(store.begin_turn(thread_id=thread_id, skill_ttl_turns=3))
    asyncio.run(
        store.retain_information(
            thread_id=thread_id,
            context_key="device_guidance:step:air1",
            source="devices.unboxing.advance",
            information={"current_step": "guide.parts"},
            guidance="Use until the step changes.",
            ttl_turns=None,
            token_budget=4000,
        )
    )
    state = None
    for _ in range(8):
        state = asyncio.run(store.begin_turn(thread_id=thread_id, skill_ttl_turns=3))

    assert state is not None
    assert project_working_context(state)["known_information"][0]["information"] == {"current_step": "guide.parts"}

    replaced = asyncio.run(
        store.retain_information(
            thread_id=thread_id,
            context_key="device_guidance:step:air1",
            source="devices.unboxing.advance",
            information={"current_step": "guide.controls"},
            guidance="Use until the step changes.",
            ttl_turns=None,
            token_budget=4000,
            invalidate_prefixes=("device_guidance:step:",),
        )
    )
    assert project_working_context(replaced)["known_information"][0]["information"] == {
        "current_step": "guide.controls"
    }


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value: str, **_kwargs):
        self.values[key] = value
        return True
