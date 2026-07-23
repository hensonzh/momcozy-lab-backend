import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

from app.agents.cozymate.context import (
    BusinessFactsProjector,
    BusinessFactsProjectorConfig,
)
from app.agents.cozymate import ServiceSkillId
from app.agent_runtime.tools.result import ToolResult
from app.modules.auth import CurrentUser


def test_business_facts_projector_projects_lactation_sources() -> None:
    calls = []

    async def profile_handler(context):
        calls.append({"tool_name": context.tool_name, "args": context.args})
        return ToolResult.json({"profile": {"preferred_name": "Mai"}, "assistant_hint": "do not project"})

    async def milk_status_handler(context):
        calls.append({"tool_name": context.tool_name, "args": context.args})
        return ToolResult.json({"totals": {"trend_pumped_volume_ml": 420}})

    run_id = uuid4()
    facts = asyncio.run(
        BusinessFactsProjector(
            handlers={
                "profile_read": profile_handler,
                "records_milk_status_read": milk_status_handler,
            },
            clock=lambda: datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc),
        ).project(
            actor=_actor(),
            run_id=run_id,
            service_skill_id=ServiceSkillId.MILK_MANAGEMENT,
        )
    )

    assert calls == [
        {"tool_name": "profile_read", "args": {}},
        {"tool_name": "records_milk_status_read", "args": {"days": 7, "limit": 5}},
    ]
    assert facts == {
        "schema_version": "v1",
        "loaded_at": "2026-07-08T08:00:00+00:00",
        "service_skill_id": "milk-management",
        "sources": [
            {"key": "profile", "tool_name": "profile_read"},
            {"key": "milk_status", "tool_name": "records_milk_status_read"},
        ],
        "profile": {"profile": {"preferred_name": "Mai"}},
        "milk_status": {"totals": {"trend_pumped_volume_ml": 420}},
    }
    assert "assistant_hint" not in facts["profile"]


def test_business_facts_projector_reads_sources_without_parallel_shared_session_access() -> None:
    session_guard = FakeSharedSessionGuard()

    async def profile_handler(context):
        await session_guard.enter("profile_read")
        try:
            return ToolResult.json({"profile": {"preferred_name": "Mai"}})
        finally:
            session_guard.exit("profile_read")

    async def milk_status_handler(context):
        await session_guard.enter("records_milk_status_read")
        try:
            return ToolResult.json({"totals": {"trend_pumped_volume_ml": 420}})
        finally:
            session_guard.exit("records_milk_status_read")

    facts = asyncio.run(
        BusinessFactsProjector(
            handlers={
                "profile_read": profile_handler,
                "records_milk_status_read": milk_status_handler,
            },
            clock=lambda: datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc),
        ).project(
            actor=_actor(),
            run_id=uuid4(),
            service_skill_id=ServiceSkillId.MILK_MANAGEMENT,
        )
    )

    assert session_guard.calls == ["profile_read", "records_milk_status_read"]
    assert facts["sources"] == [
        {"key": "profile", "tool_name": "profile_read"},
        {"key": "milk_status", "tool_name": "records_milk_status_read"},
    ]
    assert facts["profile"] == {"profile": {"preferred_name": "Mai"}}
    assert facts["milk_status"] == {"totals": {"trend_pumped_volume_ml": 420}}


def test_business_facts_projector_uses_compact_postpartum_limits() -> None:
    calls = []

    async def handler(context):
        calls.append({"tool_name": context.tool_name, "args": context.args})
        return ToolResult.json({"ok": True})

    asyncio.run(
        BusinessFactsProjector(
            handlers={
                "profile_read": handler,
                "plans_current_read": handler,
                "records_milk_summary_read": handler,
            },
            config=BusinessFactsProjectorConfig(default_limit=4, recent_limit=2, milk_days=14),
        ).project(
            actor=_actor(),
            run_id=uuid4(),
            service_skill_id=ServiceSkillId.HEALTH_CONSULTATION,
        )
    )

    assert calls == [
        {"tool_name": "profile_read", "args": {}},
        {"tool_name": "plans_current_read", "args": {"limit": 4}},
        {"tool_name": "records_milk_summary_read", "args": {"days": 14, "limit": 2}},
    ]


def test_business_facts_projector_returns_empty_when_no_handlers_are_available() -> None:
    facts = asyncio.run(
        BusinessFactsProjector(handlers={}).project(
            actor=_actor(),
            run_id=uuid4(),
            service_skill_id=ServiceSkillId.EMOTION_SUPPORT,
        )
    )

    assert facts == {}


def test_business_facts_projector_unwraps_standard_tool_results() -> None:
    async def pregnancy_context_handler(_context):
        return ToolResult.json(
            {
                "profile": {"age": 32},
                "plans": [],
                "assistant_hint": "do not project",
            }
        )

    facts = asyncio.run(
        BusinessFactsProjector(
            handlers={"pregnancy_plan_context_read": pregnancy_context_handler},
            clock=lambda: datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc),
        ).project(
            actor=_actor(),
            run_id=uuid4(),
            service_skill_id=ServiceSkillId.BIRTH_PREP,
        )
    )

    assert facts["pregnancy"] == {
        "profile": {"age": 32},
        "plans": [],
    }
    assert json.loads(json.dumps(facts, ensure_ascii=False)) == facts


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
