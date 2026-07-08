import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.agent_runtime.models import (
    AgentAction,
    AgentArtifact,
    AgentEvent,
    AgentMemory,
    AgentMessage,
    AgentRun,
    AgentRunSummary,
    AgentToolCall,
)
from production_backend.app.modules.agent_runtime.routing import IntentItem, RoutingPlan, RoutingSource, ServiceSkillId
from production_backend.app.modules.agent_runtime.run_lifecycle.executor import AgentRuntimeExecutor
from production_backend.app.modules.agent_runtime.sdk import (
    OpenAIAgentsSdkRunner,
    SdkNodeRequest,
    SdkNodeResult,
    ScriptedSdkBackend,
    scripted_sdk_response,
    scripted_tool_invocation,
)
from production_backend.app.modules.agent_runtime.skill_registry import default_service_skill_registry
from production_backend.app.modules.agent_runtime.tools import ToolExecutor, ToolGroup, ToolGroupRegistry, ToolHandlerContext, default_tool_registry


def test_agent_runtime_executor_uses_internal_ledger_context_and_sdk_result() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id, prompt_version="prompt-v2")
    prior_user = _message(thread_id=thread_id, run_id=uuid4(), role="user", text="What did we discuss?", sequence=1)
    prior_assistant = _message(thread_id=thread_id, run_id=uuid4(), role="assistant", text="Your care plan.", sequence=2)
    current_user = _message(
        thread_id=thread_id,
        run_id=run.id,
        role="user",
        text="Summarize it.",
        sequence=3,
        content_overrides={
            "timezone": "Asia/Shanghai",
            "locale": "zh-CN",
            "location": {"country": "CN", "region": "Shanghai", "city": "Shanghai"},
        },
    )
    repository = FakeRuntimeRepository(messages=[prior_user, prior_assistant, current_user], current_message=current_user)
    backend = CapturingSdkBackend(result=SdkNodeResult(final_text="Here is the summary."))
    checkpoint_store = FakeCheckpointStore()
    state_store = FakeStateStore()
    fixed_now = datetime(2026, 7, 8, 6, 30, tzinfo=timezone.utc)

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            checkpoint_store=checkpoint_store,
            state_store=state_store,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
            clock=lambda: fixed_now,
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert result.final_text == "Here is the summary."
    request = backend.requests[0]
    assert request.run_id == str(run.id)
    assert request.thread_id == str(thread_id)
    assert request.prompt_version == "prompt-v2"
    assert request.service_skill_id == "general_assistant"
    assert "你叫 CozyMate，来自 Momcozy 团队。" in request.instructions
    assert "已选择服务技能：general_assistant" in request.instructions
    assert request.tool_names == ("business.context.read", "profile.read")
    assert [item["role"] for item in request.model_input] == [
        "system",
        "developer",
        "user",
        "assistant",
        "developer",
        "user",
    ]
    assert request.model_input[0]["content"].startswith("# 全局规则")
    runtime_context = _runtime_context(request)
    assert runtime_context["state"] == {
        "service_skill_id": "general_assistant_v1",
        "service_skill_version": "v1",
        "selected_tool_group_ids": ["general.base"],
        "execution_mode": "single",
        "needs_clarification": False,
        "safety_flags": [],
    }
    assert "run_id" not in runtime_context["state"]
    assert "thread_id" not in runtime_context["state"]
    assert "actor_user_id" not in runtime_context["state"]
    assert runtime_context["user_context"] == {
        "current_time": "2026-07-08T14:30:00+08:00",
        "timezone": "Asia/Shanghai",
        "locale": "zh-CN",
        "location": {"country": "CN", "region": "Shanghai", "city": "Shanghai"},
    }
    assert runtime_context["thread_summary"] == {}
    assert runtime_context["recent_run_facts"] == []
    assert repository.routing_decisions[0]["selected_skill_id"] == "general_assistant"
    assert repository.routing_decisions[0]["routing_source"] == "fallback"
    assert repository.routing_decisions[0]["confidence"] == 0.55
    assert request.model_input[-1] == {"role": "user", "content": "Summarize it."}
    assert [checkpoint["state_summary"]["node_name"] for checkpoint in checkpoint_store.checkpoints] == ["sdk_reasoning", "finish"]
    assert checkpoint_store.checkpoints[0]["state_summary"]["current_user_message_id"] == str(current_user.id)
    assert state_store.projections[0]["selected_message_ids"] == [prior_user.id, prior_assistant.id, current_user.id]
    assert state_store.projections[0]["projection_summary"]["history_message_count"] == 2
    assert state_store.projections[0]["projection_summary"]["state_keys"] == [
        "execution_mode",
        "needs_clarification",
        "safety_flags",
        "selected_tool_group_ids",
        "service_skill_id",
        "service_skill_version",
    ]
    assert state_store.projections[0]["projection_summary"]["recent_run_fact_count"] == 0
    assert repository.run_summaries[0].payload["user_goal"] == "Summarize it."
    assert repository.run_summaries[0].payload["assistant_conclusion"] == "Here is the summary."


def test_agent_runtime_executor_projects_active_memory_into_dynamic_context() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="How should you remind me?", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    backend = CapturingSdkBackend(result=SdkNodeResult(final_text="I will keep reminders concise."))
    state_store = FakeStateStore()
    memory_service = FakeMemoryService(
        memories=[
            AgentMemory(
                id=uuid4(),
                owner_user_id=run.actor_user_id,
                memory_type="communication_preference",
                content={"summary": "Prefers concise reminders", "raw_evidence": "do not project this"},
                confidence_score=90,
                status="active",
                updated_at=datetime(2026, 7, 4, 8, 30, tzinfo=timezone.utc),
            )
        ]
    )

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            state_store=state_store,
            memory_service=memory_service,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    memory_facts = _runtime_context(backend.requests[0])["memory"]
    assert result.status == "completed"
    assert memory_service.calls == [{"owner_user_id": run.actor_user_id, "memory_type": None, "limit": 5}]
    assert memory_facts == [
        {
            "memory_id": str(memory_service.memories[0].id),
            "memory_type": "communication_preference",
            "summary": "Prefers concise reminders",
            "confidence_score": 90,
            "updated_at": "2026-07-04T08:30:00+00:00",
        }
    ]
    assert "raw_evidence" not in memory_facts[0]
    assert state_store.projections[0]["projection_summary"]["memory_count"] == 1
    assert state_store.projections[0]["projection_summary"]["fresh_business_fact_keys"] == []


def test_default_service_skills_are_file_backed() -> None:
    registry = default_service_skill_registry()

    for service_skill_id in (
        "general_assistant",
        "pregnancy_service",
        "lactation",
        "postpartum_recovery",
        "after_sales",
        "safety_guardrail",
    ):
        service_skill = registry.get(service_skill_id)
        assert service_skill.service_skill_id == service_skill_id
        assert "服务技能" in service_skill.prompt_block()


def test_agent_runtime_executor_publishes_text_deltas_to_transient_stream() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Stream please", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    transient_stream = FakeTransientStream()
    backend = ScriptedSdkBackend([scripted_sdk_response(final_text="hello", text_deltas=("hel", "lo"))])

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            transient_stream=transient_stream,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert result.final_text == "hello"
    assert transient_stream.deltas == [
        {"thread_id": thread_id, "run_id": run.id, "delta": "hel"},
        {"thread_id": thread_id, "run_id": run.id, "delta": "lo"},
    ]
    assert all(event.event_type != "message.delta" for event in repository.events)


def test_agent_runtime_executor_requires_current_user_message() -> None:
    run = _run(thread_id=uuid4())
    repository = FakeRuntimeRepository(messages=[], current_message=None)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            AgentRuntimeExecutor(
                repository=repository,
                sdk_runner=OpenAIAgentsSdkRunner(backend=CapturingSdkBackend(result=SdkNodeResult(final_text="hello"))),
            ).execute(run=run)
        )

    assert exc_info.value.code == "missing_user_message"


def test_agent_runtime_executor_rejects_empty_sdk_response() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Hello", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            AgentRuntimeExecutor(
                repository=repository,
                sdk_runner=OpenAIAgentsSdkRunner(backend=CapturingSdkBackend(result=SdkNodeResult(final_text="  "))),
            ).execute(run=run)
        )

    assert exc_info.value.code == "empty_agent_response"


def test_agent_runtime_executor_routes_sdk_tool_calls_through_tool_executor() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Read my profile", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    tool_executor = FakeToolExecutor(safe_output={"profile": {"display_name": "Mai"}})
    backend = InvokingSdkBackend()

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
            tool_executor=tool_executor,
            routing_service=StaticRoutingService(
                service_skill_id=ServiceSkillId.GENERAL,
                tool_group_ids=("general.all",),
            ),
            tool_group_registry=ToolGroupRegistry(
                groups=(
                    ToolGroup(
                        id="general.all",
                        service_skill_id=ServiceSkillId.GENERAL,
                        description="测试用全量工具组。",
                        tool_contracts=tuple(default_tool_registry().names_for_sdk()),
                    ),
                )
            ),
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert result.final_text == '{"profile": {"display_name": "Mai"}}'
    assert backend.tool_names == (
        "artifacts_hospital_bag_card_create",
        "artifacts_labor_communication_card_create",
        "artifacts_lactation_summary_create",
        "artifacts_postpartum_checkin_create",
        "business_context_read",
        "devices_guidance_assets_read",
        "devices_pump_status_read",
        "diary_entry_upsert_propose",
        "diary_recent_read",
        "files_vision_summary_read",
        "hospital_bag_cart_update_propose",
        "memory_create_propose",
        "notifications_milk_reminder_propose",
        "plans_current_read",
        "plans_milk_plan_propose",
        "plans_task_complete_propose",
        "plans_task_create_propose",
        "pregnancy_plan_context_read",
        "pregnancy_plan_create_propose",
        "profile_read",
        "records_feeding_record_propose",
        "records_milk_status_read",
        "records_milk_summary_read",
        "records_pumping_record_propose",
        "support_ticket_propose",
    )
    assert backend.tool_schemas["artifacts_hospital_bag_card_create"]["required"] == ["title"]
    assert backend.tool_schemas["artifacts_hospital_bag_card_create"]["properties"]["sections"]["maxItems"] == 20
    assert backend.tool_schemas["artifacts_labor_communication_card_create"]["properties"]["source_context"]["type"] == "object"
    assert backend.tool_schemas["business_context_read"]["properties"]["limit"]["maximum"] == 20
    assert backend.tool_schemas["devices_guidance_assets_read"]["properties"]["limit"]["maximum"] == 20
    assert backend.tool_schemas["devices_guidance_assets_read"]["properties"]["content_type"]["type"] == "string"
    assert backend.tool_schemas["devices_pump_status_read"]["properties"]["limit"]["maximum"] == 20
    assert backend.tool_schemas["diary_entry_upsert_propose"]["required"] == ["entry_date"]
    assert backend.tool_schemas["diary_entry_upsert_propose"]["properties"]["content"]["maxLength"] == 5000
    assert backend.tool_schemas["diary_recent_read"]["properties"]["limit"]["maximum"] == 20
    assert backend.tool_schemas["files_vision_summary_read"]["required"] == ["file_id"]
    assert backend.tool_schemas["hospital_bag_cart_update_propose"]["required"] == ["cart_update"]
    assert backend.tool_schemas["hospital_bag_cart_update_propose"]["additionalProperties"] is False
    assert backend.tool_schemas["memory_create_propose"]["required"] == ["memory_type", "content"]
    assert backend.tool_schemas["memory_create_propose"]["properties"]["content"]["required"] == ["summary"]
    assert "health" in backend.tool_schemas["memory_create_propose"]["properties"]["sensitivity"]["enum"]
    assert backend.tool_schemas["memory_create_propose"]["properties"]["expires_in_days"]["maximum"] == 365
    assert backend.tool_schemas["notifications_milk_reminder_propose"]["required"] == ["title"]
    assert backend.tool_schemas["plans_current_read"]["properties"]["limit"]["maximum"] == 20
    assert backend.tool_schemas["plans_milk_plan_propose"]["required"] == ["title"]
    assert backend.tool_schemas["plans_task_complete_propose"]["required"] == ["task_id"]
    assert backend.tool_schemas["plans_task_complete_propose"]["properties"]["completed"]["type"] == "boolean"
    assert backend.tool_schemas["plans_task_create_propose"]["required"] == ["title"]
    assert backend.tool_schemas["pregnancy_plan_context_read"]["properties"]["limit"]["maximum"] == 20
    assert backend.tool_schemas["pregnancy_plan_create_propose"]["required"] == ["title"]
    assert backend.tool_schemas["profile_read"]["additionalProperties"] is False
    assert backend.tool_schemas["profile_read"]["properties"] == {}
    assert backend.tool_schemas["records_feeding_record_propose"]["required"] == ["feed_time", "feed_type"]
    assert backend.tool_schemas["records_milk_status_read"]["properties"]["days"]["maximum"] == 30
    assert backend.tool_schemas["records_milk_summary_read"]["properties"]["days"]["maximum"] == 30
    assert backend.tool_schemas["records_pumping_record_propose"]["required"] == ["pump_start_time"]
    assert backend.tool_schemas["support_ticket_propose"]["required"] == ["issue_summary"]
    assert backend.tool_schemas["support_ticket_propose"]["additionalProperties"] is False
    assert tool_executor.calls[0]["actor"].user_id == run.actor_user_id
    assert tool_executor.calls[0]["run_id"] == run.id
    assert tool_executor.calls[0]["tool_name"] == "profile.read"
    assert tool_executor.calls[0]["args"] == {}


def test_agent_runtime_executor_selects_service_skill_and_scopes_tools_by_group() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="今天奶量怎么样？", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    backend = CapturingSdkBackend(result=SdkNodeResult(final_text="I will review milk records."))

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    request = backend.requests[0]
    assert result.status == "completed"
    assert request.service_skill_id == "lactation"
    assert "已选择服务技能：lactation" in request.instructions
    state = _runtime_context(request)["state"]
    assert state["service_skill_id"] == "lactation_v1"
    assert state["selected_tool_group_ids"] == ["general.base", "lactation.milk_read"]
    assert request.tool_names == (
        "business.context.read",
        "profile.read",
        "records.milk_status.read",
        "records.milk_summary.read",
    )


def test_agent_runtime_executor_injects_pregnancy_service_skill() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="帮我准备待产包和分娩沟通单", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    backend = CapturingSdkBackend(result=SdkNodeResult(final_text="我先帮你确认关键信息。"))
    state_store = FakeStateStore()

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            state_store=state_store,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    request = backend.requests[0]
    state = _runtime_context(request)["state"]
    assert result.status == "completed"
    assert request.service_skill_id == "pregnancy_service"
    assert state["service_skill_id"] == "pregnancy_service_v1"
    assert state["service_skill_version"] == "v1"
    assert "CozyMate" in request.instructions
    assert "服务技能" in request.instructions
    assert "待产包清单" in request.instructions
    assert "分娩沟通单" in request.instructions
    assert "每轮只推进一个重点" in request.instructions
    assert "不要调用 load_skill" in request.instructions
    assert "artifacts.hospital_bag_card.create" in request.tool_names
    assert "artifacts.labor_communication_card.create" in request.tool_names
    assert state_store.projections[0]["projection_summary"]["service_skill_id"] == "pregnancy_service_v1"


def test_agent_runtime_executor_projects_recent_run_facts_into_dynamic_context() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    previous_run_id = uuid4()
    previous_summary = AgentRunSummary(
        id=uuid4(),
        run_id=previous_run_id,
        thread_id=thread_id,
        owner_user_id=run.actor_user_id,
        service_skill_id="lactation",
        summary_type="run_fact",
        schema_version="v1",
        payload={
            "user_goal": "昨天奶量怎么样？",
            "assistant_conclusion": "昨天总奶量偏低，建议今天观察补水和吸奶频率。",
            "tools_used": ["records.milk_summary.read"],
            "tool_facts": [{"tool_name": "records.milk_summary.read", "safe_output": {"total_ml": 420}}],
            "verbose_unused": "x" * 3000,
        },
        source_message_ids=[],
        source_tool_call_ids=[],
        created_at=datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc),
    )
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="那今天怎么安排？", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user, run_summaries=[previous_summary])
    backend = CapturingSdkBackend(result=SdkNodeResult(final_text="今天先看最近一次记录。"))

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    recent_run_facts = _runtime_context(backend.requests[0])["recent_run_facts"]
    assert result.status == "completed"
    assert recent_run_facts == [
        {
            "run_id": str(previous_run_id),
            "service_skill_id": "lactation",
            "schema_version": "v1",
            "created_at": "2026-07-07T10:00:00+00:00",
            "facts": {
                "user_goal": "昨天奶量怎么样？",
                "assistant_conclusion": "昨天总奶量偏低，建议今天观察补水和吸奶频率。",
                "tools_used": ["records.milk_summary.read"],
                "tool_facts": [{"tool_name": "records.milk_summary.read", "safe_output": {"total_ml": 420}}],
                "verbose_unused": ("x" * 2400) + "...",
            },
        }
    ]
    assert repository.run_summaries[-1].run_id == run.id
    assert repository.run_summaries[-1].payload["assistant_conclusion"] == "今天先看最近一次记录。"


def test_agent_runtime_executor_routes_named_pump_issue_to_device_service_skill() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(
        thread_id=thread_id,
        run_id=run.id,
        role="user",
        text="My Air1 pump suction feels weak today. What should I check?",
        sequence=1,
    )
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    backend = CapturingSdkBackend(result=SdkNodeResult(final_text="I will check device status."))

    asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    request = backend.requests[0]
    assert request.service_skill_id == "after_sales"
    assert request.tool_names == (
        "business.context.read",
        "devices.guidance_assets.read",
        "devices.pump_status.read",
        "files.vision_summary.read",
        "profile.read",
    )


def test_agent_runtime_executor_excludes_tool_messages_from_conversation_history() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    prior_user = _message(thread_id=thread_id, run_id=uuid4(), role="user", text="Read my milk summary.", sequence=1)
    prior_tool = _message(thread_id=thread_id, run_id=uuid4(), role="tool", text='{"records": []}', sequence=2)
    prior_assistant = _message(thread_id=thread_id, run_id=uuid4(), role="assistant", text="I checked your summary.", sequence=3)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="What next?", sequence=4)
    repository = FakeRuntimeRepository(
        messages=[prior_user, prior_tool, prior_assistant, current_user],
        current_message=current_user,
    )
    backend = CapturingSdkBackend(result=SdkNodeResult(final_text="Next step."))

    asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    history = backend.requests[0].model_input[2:-2]
    assert history == [
        {"role": "user", "content": "Read my milk summary."},
        {"role": "assistant", "content": "I checked your summary."},
    ]


def test_agent_runtime_executor_real_tool_executor_uses_run_actor_role_permissions() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Read my profile", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user, run=run)
    tool_executor = ToolExecutor(
        registry=default_tool_registry(),
        repository=repository,
        handlers={"profile.read": profile_read_handler},
    )
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text="Profile context loaded.",
                tool_invocations=(scripted_tool_invocation("profile.read"),),
                expected_available_tools=("profile.read",),
            )
        ]
    )

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
            tool_executor=tool_executor,
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert backend.requests[0].run_id == str(run.id)
    assert repository.tool_call is not None
    assert repository.tool_call.status == "completed"
    assert repository.tool_call.safe_args == {}
    assert repository.events[0].event_type == "tool.started"
    assert repository.events[1].event_type == "tool.completed"
    assert result.final_text == "Profile context loaded."
    assert repository.run_summaries[0].payload["tools_used"] == ["profile.read"]
    assert repository.run_summaries[0].payload["tool_facts"] == [
        {
            "tool_call_id": str(repository.tool_call.id),
            "tool_name": "profile.read",
            "status": "completed",
            "safe_output": {"profile": {"actor_user_id": str(run.actor_user_id)}},
        }
    ]


def test_agent_runtime_executor_persists_sdk_action_proposal_and_waits_for_confirmation() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Create a support ticket", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    checkpoint_store = FakeCheckpointStore()
    backend = CapturingSdkBackend(
        result=SdkNodeResult(
            action_proposals=[
                {
                    "action_type": "support.ticket.create",
                    "target_type": "support_ticket",
                    "side_effect_level": "medium",
                    "preview_payload": {"summary": "Pump does not turn on"},
                    "apply_payload": {"issue_summary": "Pump does not turn on"},
                    "idempotency_key": "idem-action",
                }
            ]
        )
    )

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            checkpoint_store=checkpoint_store,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    assert result.status == "waiting_for_confirmation"
    assert result.pending_action_id == repository.actions[0].id
    assert repository.actions[0].status == "confirmation_required"
    assert repository.actions[0].apply_payload == {"issue_summary": "Pump does not turn on"}
    assert repository.events[-1].event_type == "action.confirmation_required"
    assert repository.events[-1].payload["action_id"] == str(repository.actions[0].id)
    assert repository.events[-1].payload["action_status"] == "confirmation_required"
    assert repository.events[-1].payload["action_type"] == "support.ticket.create"
    assert repository.events[-1].payload["target_type"] == "support_ticket"
    assert repository.events[-1].payload["side_effect_level"] == "medium"
    assert repository.events[-1].payload["preview_payload"] == {"summary": "Pump does not turn on"}
    assert "apply_payload" not in repository.events[-1].payload
    assert [checkpoint["state_summary"]["node_name"] for checkpoint in checkpoint_store.checkpoints] == [
        "sdk_reasoning",
        "confirmation_interrupt",
    ]
    assert checkpoint_store.checkpoints[-1]["state_summary"]["pending_action_id"] == str(repository.actions[0].id)


def test_agent_runtime_executor_rejects_unsupported_sdk_action_proposal_before_persisting() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Delete my device", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    backend = CapturingSdkBackend(
        result=SdkNodeResult(
            action_proposals=[
                {
                    "action_type": "device.delete",
                    "target_type": "device",
                    "side_effect_level": "high",
                    "preview_payload": {"summary": "Delete device"},
                    "apply_payload": {"device_id": "device_1"},
                }
            ]
        )
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            AgentRuntimeExecutor(
                repository=repository,
                sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
            ).execute(run=run)
        )

    assert exc_info.value.code == "unsupported_agent_action"
    assert repository.actions == []
    assert repository.events == []


def test_agent_runtime_executor_rejects_direct_apply_sdk_action_proposal_before_persisting() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Add a feeding record", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    backend = CapturingSdkBackend(
        result=SdkNodeResult(
            action_proposals=[
                {
                    "action_type": "records.feeding_record.create",
                    "target_type": "feeding_record",
                    "side_effect_level": "low",
                    "preview_payload": {"feed_type": "bottle"},
                    "apply_payload": {"feed_time": "2026-07-04T08:30:00Z", "feed_type": "bottle"},
                }
            ]
        )
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            AgentRuntimeExecutor(
                repository=repository,
                sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
            ).execute(run=run)
        )

    assert exc_info.value.code == "direct_agent_action_requires_tool"
    assert repository.actions == []
    assert repository.events == []


def test_agent_runtime_executor_rejects_multiple_sdk_action_proposals_before_side_effects() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Create two tickets", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    backend = CapturingSdkBackend(
        result=SdkNodeResult(
            final_text="I drafted a plan.",
            artifacts=[{"artifact_type": "care_plan", "payload": {"title": "Plan"}}],
            action_proposals=[
                {"action_type": "support.ticket.create", "apply_payload": {"issue_summary": "First"}},
                {"action_type": "support.ticket.create", "apply_payload": {"issue_summary": "Second"}},
            ],
        )
    )

    with pytest.raises(ApiError) as exc_info:
        asyncio.run(
            AgentRuntimeExecutor(
                repository=repository,
                sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
            ).execute(run=run)
        )

    assert exc_info.value.code == "too_many_agent_action_proposals"
    assert repository.actions == []
    assert repository.artifacts == []
    assert repository.events == []


def test_agent_runtime_executor_persists_sdk_artifacts_and_emits_events() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Create a plan", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    backend = CapturingSdkBackend(
        result=SdkNodeResult(
            final_text="I drafted a plan.",
            artifacts=[
                {
                    "artifact_type": "care_plan",
                    "schema_version": "v1",
                    "payload": {"title": "Birth plan"},
                }
            ],
        )
    )

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert repository.artifacts[0].artifact_type == "care_plan"
    assert repository.artifacts[0].payload == {"title": "Birth plan"}
    assert repository.events[0].event_type == "artifact.created"
    assert repository.events[0].payload["artifact_id"] == str(repository.artifacts[0].id)


def test_agent_runtime_executor_externalizes_large_sdk_artifacts() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Create a plan", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    storage = FakeObjectStorage()
    payload = {"title": "Birth plan", "sections": [{"text": "x" * 200}]}
    backend = CapturingSdkBackend(
        result=SdkNodeResult(
            final_text="I drafted a plan.",
            artifacts=[
                {
                    "artifact_type": "care_plan",
                    "schema_version": "v1",
                    "payload": payload,
                }
            ],
        )
    )

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
            object_storage=storage,
            max_inline_artifact_payload_bytes=80,
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert repository.artifacts[0].raw_payload_ref == "memory://agent-runtime/artifacts"
    assert repository.artifacts[0].payload["_externalized_payload"]["stored"] is True
    assert "uri" not in repository.artifacts[0].payload["_externalized_payload"]
    assert "key" not in repository.artifacts[0].payload["_externalized_payload"]
    assert repository.artifacts[0].payload["payload_summary"] == payload
    assert json.loads(storage.body.decode("utf-8")) == payload


class CapturingSdkBackend:
    def __init__(self, *, result: SdkNodeResult) -> None:
        self.result = result
        self.requests = []

    async def run(self, request: SdkNodeRequest) -> SdkNodeResult:
        self.requests.append(request)
        return self.result


class StaticRoutingService:
    def __init__(self, *, service_skill_id: ServiceSkillId, tool_group_ids: tuple[str, ...]) -> None:
        self.service_skill_id = service_skill_id
        self.tool_group_ids = tool_group_ids

    async def route(self, ctx):
        return RoutingPlan(
            selected_skill_id=self.service_skill_id,
            intents=[IntentItem(intent_type=f"{self.service_skill_id.value}_request", service_skill_id=self.service_skill_id)],
            tool_group_ids=list(self.tool_group_ids),
            execution_mode="single",
            confidence=1,
            source=RoutingSource.MODEL_PLANNER,
            reason_codes=["test_static_plan"],
        )


def _runtime_context(request: SdkNodeRequest) -> dict:
    return request.model_input[-2]["content"]["runtime_context"]


class FakeRuntimeRepository:
    def __init__(
        self,
        *,
        messages: list[AgentMessage],
        current_message: AgentMessage | None,
        run: AgentRun | None = None,
        run_summaries: list[AgentRunSummary] | None = None,
    ) -> None:
        self.messages = messages
        self.current_message = current_message
        self.run = run
        self.actions = []
        self.artifacts = []
        self.events = []
        self.routing_decisions = []
        self.tool_call = None
        self.tool_output = None
        self.run_summaries = list(run_summaries or [])

    async def get_latest_user_message_for_run(self, *, run_id):
        if self.current_message is not None and self.current_message.run_id == run_id:
            return self.current_message
        return None

    async def list_messages_for_thread(self, *, thread_id, limit=40):
        return [message for message in self.messages if message.thread_id == thread_id][:limit]

    async def get_run(self, *, run_id):
        if self.run is not None and self.run.id == run_id:
            return self.run
        if self.current_message is not None and self.current_message.run_id == run_id:
            return _run(thread_id=self.current_message.thread_id, run_id=run_id)
        return None

    async def record_routing_decision(self, **kwargs):
        self.routing_decisions.append(kwargs)
        return kwargs

    async def start_tool_call(self, **kwargs):
        self.tool_call = AgentToolCall(
            id=uuid4(),
            run_id=kwargs["run_id"],
            tool_name=kwargs["tool_name"],
            call_id=kwargs["call_id"],
            status="started",
            safe_args=kwargs["safe_args"],
            started_at=kwargs["started_at"],
            error_code="",
        )
        return self.tool_call

    async def complete_tool_call(self, **kwargs):
        self.tool_call.status = "completed"
        self.tool_call.completed_at = kwargs["completed_at"]
        return self.tool_call

    async def fail_tool_call(self, **kwargs):
        self.tool_call.status = "failed"
        self.tool_call.completed_at = kwargs["completed_at"]
        self.tool_call.error_code = kwargs["error_code"]
        return self.tool_call

    async def create_tool_output(self, **kwargs):
        self.tool_output = FakeToolOutput(
            tool_call_id=kwargs["tool_call_id"],
            safe_output=kwargs["safe_output"],
            raw_output_ref=kwargs.get("raw_output_ref", ""),
        )
        return self.tool_output

    async def create_action(self, **kwargs):
        action = AgentAction(
            id=uuid4(),
            run_id=kwargs["run_id"],
            actor_user_id=kwargs["actor_user_id"],
            action_type=kwargs["action_type"],
            target_type=kwargs["target_type"],
            target_id=kwargs["target_id"],
            status=kwargs["status"],
            side_effect_level=kwargs["side_effect_level"],
            preview_payload=kwargs["preview_payload"],
            apply_payload=kwargs["apply_payload"],
            idempotency_key=kwargs["idempotency_key"],
            expires_at=kwargs["expires_at"],
            error_code="",
        )
        self.actions.append(action)
        return action

    async def create_artifact(self, **kwargs):
        artifact = AgentArtifact(
            id=uuid4(),
            run_id=kwargs["run_id"],
            owner_user_id=kwargs["owner_user_id"],
            artifact_type=kwargs["artifact_type"],
            schema_version=kwargs["schema_version"],
            status=kwargs["status"],
            payload=kwargs["payload"],
            raw_payload_ref=kwargs["raw_payload_ref"],
        )
        self.artifacts.append(artifact)
        return artifact

    async def append_event(self, **kwargs):
        event = AgentEvent(
            event_id=uuid4(),
            thread_id=kwargs["thread_id"],
            run_id=kwargs["run_id"],
            sequence=len(self.events) + 1,
            event_type=kwargs["event_type"],
            payload=kwargs["payload"],
        )
        self.events.append(event)
        return event

    async def list_actions_for_run(self, *, run_id):
        return [action for action in self.actions if action.run_id == run_id]

    async def list_artifacts_for_run(self, *, run_id):
        return [artifact for artifact in self.artifacts if artifact.run_id == run_id]

    async def list_tool_outputs_for_run(self, *, run_id):
        if self.tool_call is None or self.tool_output is None or self.tool_call.run_id != run_id:
            return []
        return [(self.tool_call, self.tool_output)]

    async def list_recent_run_summaries(self, *, thread_id, owner_user_id, limit, summary_type="run_fact", exclude_run_id=None):
        summaries = [
            summary
            for summary in self.run_summaries
            if summary.thread_id == thread_id
            and summary.owner_user_id == owner_user_id
            and summary.summary_type == summary_type
            and summary.run_id != exclude_run_id
        ]
        return summaries[-limit:]

    async def get_latest_thread_summary(self, *, thread_id, owner_user_id):
        for summary in reversed(self.run_summaries):
            if summary.thread_id == thread_id and summary.owner_user_id == owner_user_id and summary.summary_type == "thread_summary":
                return summary
        return None

    async def upsert_run_summary(self, **kwargs):
        for summary in self.run_summaries:
            if summary.run_id == kwargs["run_id"] and summary.summary_type == kwargs["summary_type"]:
                summary.service_skill_id = kwargs["service_skill_id"]
                summary.schema_version = kwargs["schema_version"]
                summary.payload = kwargs["payload"]
                summary.source_message_ids = kwargs["source_message_ids"]
                summary.source_tool_call_ids = kwargs["source_tool_call_ids"]
                return summary
        summary = AgentRunSummary(
            id=uuid4(),
            run_id=kwargs["run_id"],
            thread_id=kwargs["thread_id"],
            owner_user_id=kwargs["owner_user_id"],
            service_skill_id=kwargs["service_skill_id"],
            summary_type=kwargs["summary_type"],
            schema_version=kwargs["schema_version"],
            payload=kwargs["payload"],
            source_message_ids=kwargs["source_message_ids"],
            source_tool_call_ids=kwargs["source_tool_call_ids"],
            created_at=datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc),
        )
        self.run_summaries.append(summary)
        return summary


class FakeCheckpointStore:
    def __init__(self) -> None:
        self.checkpoints = []

    async def save_run_checkpoint(self, **kwargs):
        self.checkpoints.append(kwargs)


class FakeStateStore:
    def __init__(self) -> None:
        self.projections = []

    async def record_context_projection(self, **kwargs):
        self.projections.append(kwargs)


class FakeMemoryService:
    def __init__(self, *, memories: list[AgentMemory]) -> None:
        self.memories = memories
        self.calls = []

    async def list_active_memories(self, *, owner_user_id, memory_type=None, limit=20):
        self.calls.append({"owner_user_id": owner_user_id, "memory_type": memory_type, "limit": limit})
        return [memory for memory in self.memories if memory.owner_user_id == owner_user_id and memory.status == "active"][:limit]


class FakeTransientStream:
    def __init__(self) -> None:
        self.deltas = []

    async def publish_message_delta(self, *, thread_id, run_id, delta, message_stream_id="assistant", ttl_seconds=600):
        self.deltas.append({"thread_id": thread_id, "run_id": run_id, "delta": delta})
        return None


class FakeToolExecutor:
    def __init__(self, *, safe_output):
        self.safe_output = safe_output
        self.calls = []

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return FakeToolExecutionResult(safe_output=self.safe_output)


class FakeToolExecutionResult:
    def __init__(self, *, safe_output):
        self.safe_output = safe_output


class FakeToolOutput:
    def __init__(self, *, tool_call_id, safe_output, raw_output_ref=""):
        self.id = uuid4()
        self.tool_call_id = tool_call_id
        self.safe_output = safe_output
        self.raw_output_ref = raw_output_ref


class FakeObjectStorage:
    def __init__(self) -> None:
        self.key = ""
        self.body = b""
        self.content_type = ""

    async def put_bytes(self, *, key, body, content_type):
        self.key = key
        self.body = body
        self.content_type = content_type
        return FakeStoredObject(
            key=key,
            uri="memory://agent-runtime/artifacts",
            size_bytes=len(body),
            content_type=content_type,
        )

    async def get_bytes(self, *, key):
        return self.body

    async def delete(self, *, key):
        return None


class FakeStoredObject:
    def __init__(self, *, key, uri, size_bytes, content_type):
        self.key = key
        self.uri = uri
        self.size_bytes = size_bytes
        self.content_type = content_type


class InvokingSdkBackend:
    def __init__(self) -> None:
        self.tool_names = ()
        self.tool_schemas = {}

    async def run(self, request: SdkNodeRequest) -> SdkNodeResult:
        self.tool_names = tuple(tool.sdk_name for tool in request.tools)
        self.tool_schemas = {tool.sdk_name: tool.params_json_schema for tool in request.tools}
        profile_tool = next(tool for tool in request.tools if tool.contract_name == "profile.read")
        output = await profile_tool.invoke_json("{}")
        return SdkNodeResult(final_text=output)


async def profile_read_handler(context: ToolHandlerContext):
    return {"profile": {"actor_user_id": str(context.actor.user_id)}}


def _run(*, thread_id, run_id=None, prompt_version: str = "") -> AgentRun:
    return AgentRun(
        id=run_id or uuid4(),
        thread_id=thread_id,
        actor_user_id=uuid4(),
        status="running",
        runtime_pattern="langgraph_sdk",
        graph_version="momcozy-agent-v1",
        prompt_version=prompt_version,
        request_id="req",
        trace_id="trace",
        error_code="",
        error_details={},
    )


def _message(*, thread_id, run_id, role: str, text: str, sequence: int, content_overrides: dict[str, Any] | None = None) -> AgentMessage:
    content = {"text": text}
    if content_overrides:
        content.update(content_overrides)
    return AgentMessage(
        id=uuid4(),
        thread_id=thread_id,
        run_id=run_id,
        role=role,
        message_type="text",
        content=content,
        status="completed",
        sequence=sequence,
    )
