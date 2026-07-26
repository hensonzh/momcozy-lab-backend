import asyncio
from datetime import date
from uuid import uuid4

from app.agent_runtime.tools.executor import ToolHandlerContext
from app.agents.cozymate.tools import default_tool_registry
from app.agents.cozymate.tools.handlers.schedule import ScheduleTimelineReadToolHandler
from app.modules.auth import CurrentUser
from app.modules.plans.schedule_timeline_schema import (
    ScheduleTimelineCounts,
    ScheduleTimelineReadOutput,
)


def test_schedule_timeline_read_contract_is_cross_domain_and_read_only() -> None:
    contract = default_tool_registry().get("schedule_timeline_read")

    assert contract.domain == "schedule_timeline"
    assert contract.effect_scope == "none"
    assert "跨领域日程时间线" in contract.description
    assert "实际业务记录" in contract.description
    assert set(contract.input_schema["properties"]) == {
        "start_date",
        "end_date",
        "domains",
        "states",
        "limit",
    }
    assert contract.input_schema["properties"]["domains"]["items"]["enum"] == [
        "lactation",
        "pregnancy",
        "postpartum_recovery",
        "general",
    ]
    for field_schema in contract.input_schema["properties"].values():
        assert field_schema["description"]

    output_schema = contract.output_schema
    assert output_schema is not None
    assert output_schema["additionalProperties"] is False
    assert set(output_schema["required"]) == {
        "as_of_date",
        "timezone",
        "start_date",
        "end_date",
        "domains",
        "plans",
        "items",
        "counts",
        "truncated",
    }
    for object_schema in [output_schema, *output_schema["$defs"].values()]:
        for field_name, field_schema in object_schema.get("properties", {}).items():
            assert field_schema.get("description"), f"{object_schema['title']}.{field_name} lacks a description"


def test_schedule_timeline_read_handler_uses_trusted_local_context_and_bounded_defaults() -> None:
    actor = CurrentUser(
        user_id=uuid4(),
        subject="timeline-user",
        session_id="timeline-session",
        token_id="timeline-token",
        roles=frozenset({"user"}),
        permissions=frozenset(),
    )
    service = FakeScheduleTimelineService()
    handler = ScheduleTimelineReadToolHandler(service=service)

    result = asyncio.run(
        handler.execute(
            ToolHandlerContext(
                actor=actor,
                run_id=uuid4(),
                tool_name="schedule_timeline_read",
                call_id="timeline-call",
                args={
                    "runtime_local_date": "2026-07-24",
                    "runtime_timezone": "Asia/Shanghai",
                },
                thread_id=uuid4(),
            )
        )
    )

    assert service.query == {
        "owner_user_id": actor.user_id,
        "as_of_date": date(2026, 7, 24),
        "start_date": date(2026, 7, 17),
        "end_date": date(2026, 7, 31),
        "timezone_name": "Asia/Shanghai",
        "limit": 50,
        "domains": (
            "lactation",
            "pregnancy",
            "postpartum_recovery",
            "general",
        ),
        "states": (),
    }
    assert result["domains"] == [
        "lactation",
        "pregnancy",
        "postpartum_recovery",
        "general",
    ]
    assert result["plans"] == []
    assert result["items"] == []


class FakeScheduleTimelineService:
    def __init__(self) -> None:
        self.query: dict[str, object] = {}

    async def read(self, **kwargs):
        self.query = kwargs
        return ScheduleTimelineReadOutput(
            as_of_date=kwargs["as_of_date"],
            timezone=kwargs["timezone_name"],
            start_date=kwargs["start_date"],
            end_date=kwargs["end_date"],
            domains=list(kwargs["domains"]),
            plans=[],
            items=[],
            counts=ScheduleTimelineCounts(
                pending=0,
                completed=0,
                skipped=0,
                recorded=0,
            ),
            truncated=False,
        )
