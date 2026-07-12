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


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value: str, **_kwargs):
        self.values[key] = value
        return True
