import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from app.agents.cozymate.context import BusinessFactsProjector
from app.agents.cozymate import ServiceSkillId
from app.agent_runtime.tools.result import ToolResult
from app.modules.auth import CurrentUser


def test_business_facts_projector_projects_maternal_infant_profile_source() -> None:
    calls = []

    async def lactation_context_handler(context):
        calls.append({"tool_name": context.tool_name, "args": context.args})
        return ToolResult.json({"mother": {"age": 31}, "assistant_hint": "do not project"})

    async def milk_status_handler(context):
        calls.append({"tool_name": context.tool_name, "args": context.args})
        return ToolResult.json({"totals": {"trend_pumped_volume_ml": 420}})

    run_id = uuid4()
    facts = asyncio.run(
        BusinessFactsProjector(
            handlers={
                "profile_read": lactation_context_handler,
                "milk_analysis_manage": milk_status_handler,
            },
            clock=lambda: datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc),
        ).project(
            actor=_actor(),
            run_id=run_id,
            service_skill_id=ServiceSkillId.MILK_MANAGEMENT,
        )
    )

    assert calls == [
        {
            "tool_name": "profile_read",
            "args": {"runtime_local_date": "2026-07-08"},
        },
        {
            "tool_name": "milk_analysis_manage",
            "args": {
                "operation": "review",
                "detail_level": "summary",
                "days": 7,
                "limit": 5,
            },
        },
    ]
    assert facts == {
        "schema_version": "v1",
        "loaded_at": "2026-07-08T08:00:00+00:00",
        "service_skill_id": "milk-management",
        "sources": [
            {"key": "lactation_context", "tool_name": "profile_read"},
            {"key": "milk_status", "tool_name": "milk_analysis_manage"},
        ],
        "lactation_context": {
            "mother": {"age": 31},
            "assistant_hint": "do not project",
        },
        "milk_status": {"totals": {"trend_pumped_volume_ml": 420}},
    }
    assert facts["lactation_context"]["assistant_hint"] == "do not project"


def test_business_facts_projector_reads_sources_without_parallel_shared_session_access() -> None:
    session_guard = FakeSharedSessionGuard()

    async def lactation_context_handler(context):
        await session_guard.enter("profile_read")
        try:
            return ToolResult.json({"mother": {"age": 31}})
        finally:
            session_guard.exit("profile_read")

    async def milk_status_handler(context):
        await session_guard.enter("milk_analysis_manage")
        try:
            return ToolResult.json({"totals": {"trend_pumped_volume_ml": 420}})
        finally:
            session_guard.exit("milk_analysis_manage")

    facts = asyncio.run(
        BusinessFactsProjector(
            handlers={
                "profile_read": lactation_context_handler,
                "milk_analysis_manage": milk_status_handler,
            },
            clock=lambda: datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc),
        ).project(
            actor=_actor(),
            run_id=uuid4(),
            service_skill_id=ServiceSkillId.MILK_MANAGEMENT,
        )
    )

    assert session_guard.calls == ["profile_read", "milk_analysis_manage"]
    assert facts["sources"] == [
        {"key": "lactation_context", "tool_name": "profile_read"},
        {"key": "milk_status", "tool_name": "milk_analysis_manage"},
    ]
    assert facts["lactation_context"] == {"mother": {"age": 31}}
    assert facts["milk_status"] == {"totals": {"trend_pumped_volume_ml": 420}}


def test_device_guidance_uses_unified_profile_read_with_all_babies_scope() -> None:
    calls = []

    async def profile_handler(context):
        calls.append({"tool_name": context.tool_name, "args": context.args})
        return ToolResult.json({"mother": {"preferred_name": "Mai"}})

    facts = asyncio.run(
        BusinessFactsProjector(
            handlers={"profile_read": profile_handler},
            clock=lambda: datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc),
        ).project(
            actor=_actor(),
            run_id=uuid4(),
            service_skill_id=ServiceSkillId.DEVICE_GUIDANCE,
        )
    )

    assert calls == [
        {
            "tool_name": "profile_read",
            "args": {
                "infant_scope": "all",
                "runtime_local_date": "2026-07-08",
            },
        }
    ]
    assert facts["profile"] == {"mother": {"preferred_name": "Mai"}}


def test_business_facts_projector_does_not_project_birth_prep_context() -> None:
    facts = asyncio.run(
        BusinessFactsProjector(
            handlers={},
            clock=lambda: datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc),
        ).project(
            actor=_actor(),
            run_id=uuid4(),
            service_skill_id=ServiceSkillId.BIRTH_PREP,
        )
    )

    assert facts == {}


class FakeSharedSessionGuard:
    def __init__(self) -> None:
        self.active_label = ""
        self.calls = []

    async def enter(self, label: str) -> None:
        if self.active_label:
            raise AssertionError(f"shared session used concurrently by {self.active_label} and {label}")
        self.active_label = label
        self.calls.append(label)
        await asyncio.sleep(0)

    def exit(self, label: str) -> None:
        assert self.active_label == label
        self.active_label = ""


def _actor() -> CurrentUser:
    user_id = uuid4()
    return CurrentUser(
        user_id=user_id,
        subject=str(user_id),
        session_id="",
        token_id="",
        roles=frozenset({"user"}),
        permissions=frozenset(),
    )
