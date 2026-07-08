import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from production_backend.app.modules.agent_runtime.context import BusinessFactsProjector, BusinessFactsProjectorConfig
from production_backend.app.modules.agent_runtime.routing import IntentItem, RoutingPlan, RoutingSource, ServiceSkillId
from production_backend.app.modules.auth import CurrentUser


def test_business_facts_projector_projects_lactation_sources() -> None:
    calls = []

    async def profile_handler(context):
        calls.append({"tool_name": context.tool_name, "args": context.args})
        return {"profile": {"display_name": "Mai"}, "assistant_hint": "do not project"}

    async def milk_status_handler(context):
        calls.append({"tool_name": context.tool_name, "args": context.args})
        return {"totals": {"trend_pumped_volume_ml": 420}}

    run_id = uuid4()
    facts = asyncio.run(
        BusinessFactsProjector(
            handlers={
                "profile.read": profile_handler,
                "records.milk_status.read": milk_status_handler,
            },
            clock=lambda: datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc),
        ).project(
            actor=_actor(),
            run_id=run_id,
            routing_plan=_plan(ServiceSkillId.MILK_MANAGEMENT),
        )
    )

    assert calls == [
        {"tool_name": "profile.read", "args": {}},
        {"tool_name": "records.milk_status.read", "args": {"days": 7, "limit": 5}},
    ]
    assert facts == {
        "schema_version": "v1",
        "loaded_at": "2026-07-08T08:00:00+00:00",
        "service_skill_id": "milk-management",
        "sources": [
            {"key": "profile", "tool_name": "profile.read"},
            {"key": "milk_status", "tool_name": "records.milk_status.read"},
        ],
        "profile": {"profile": {"display_name": "Mai"}},
        "milk_status": {"totals": {"trend_pumped_volume_ml": 420}},
    }
    assert "assistant_hint" not in facts["profile"]


def test_business_facts_projector_uses_compact_postpartum_limits() -> None:
    calls = []

    async def handler(context):
        calls.append({"tool_name": context.tool_name, "args": context.args})
        return {"ok": True}

    asyncio.run(
        BusinessFactsProjector(
            handlers={
                "profile.read": handler,
                "plans.current.read": handler,
                "diary.recent.read": handler,
                "records.milk_summary.read": handler,
            },
            config=BusinessFactsProjectorConfig(default_limit=4, recent_limit=2, milk_days=14),
        ).project(
            actor=_actor(),
            run_id=uuid4(),
            routing_plan=_plan(ServiceSkillId.HEALTH_CONSULTATION),
        )
    )

    assert calls == [
        {"tool_name": "profile.read", "args": {}},
        {"tool_name": "plans.current.read", "args": {"limit": 4}},
        {"tool_name": "diary.recent.read", "args": {"limit": 2}},
        {"tool_name": "records.milk_summary.read", "args": {"days": 14, "limit": 2}},
    ]


def test_business_facts_projector_returns_empty_when_no_handlers_are_available() -> None:
    facts = asyncio.run(
        BusinessFactsProjector(handlers={}).project(
            actor=_actor(),
            run_id=uuid4(),
            routing_plan=_plan(ServiceSkillId.EMOTION_SUPPORT),
        )
    )

    assert facts == {}


def _plan(skill_id: ServiceSkillId) -> RoutingPlan:
    return RoutingPlan(
        selected_skill_id=skill_id,
        intents=[IntentItem(intent_type=f"{skill_id.value}_request", service_skill_id=skill_id)],
        tool_group_ids=[],
        confidence=1,
        source=RoutingSource.MODEL_PLANNER,
    )


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
