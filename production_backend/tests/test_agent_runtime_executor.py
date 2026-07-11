import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from production_backend.app.core.errors import ApiError
from production_backend.app.modules.agent_runtime.models import (
    AgentAction,
    AgentArtifact,
    AgentEvent,
    AgentMessage,
    AgentRun,
    AgentRunSummary,
    AgentToolCall,
)
from production_backend.app.modules.agent_runtime.event_stream.sink import AgentEventSink
from production_backend.app.modules.agent_runtime.agents.cozymate_service_agent import ServiceSkillId
from production_backend.app.modules.agent_runtime.run_lifecycle.executor import (
    AgentRuntimeExecutor,
    _pregnancy_runtime_plan_context,
)
from production_backend.app.modules.agent_runtime.run_lifecycle.quick_replies import QUICK_REPLY_RESPONSE_FORMAT, QuickReplyFinalizer
from production_backend.app.modules.agent_runtime.sdk import (
    OpenAIAgentsSdkRunner,
    SdkNodeRequest,
    SdkNodeResult,
    ScriptedSdkBackend,
    scripted_sdk_response,
    scripted_tool_invocation,
)
from production_backend.app.modules.agent_runtime.agents.cozymate_service_agent.skill_registry import default_service_skill_registry
from production_backend.app.modules.agent_runtime.agents.cozymate_service_agent.tools import (
    ToolExecutor,
    ToolHandlerContext,
    default_tool_registry,
)


def test_agent_runtime_executor_uses_internal_ledger_context_and_sdk_result(caplog) -> None:
    caplog.set_level(logging.INFO, logger="production_backend.agent_runtime")
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
    assert request.service_skill_id == "cozymate_service_agent"
    assert "你叫 CozyMate，来自 Momcozy 团队。" in request.instructions
    assert "制定孕期计划" not in request.instructions
    assert "奶量管理仅处理三类任务" not in request.instructions
    assert "已选择服务技能" not in request.instructions
    assert request.tool_names == ("load_service_skill",)
    assert [item["role"] for item in request.model_input] == [
        "user",
        "assistant",
        "developer",
        "user",
    ]
    assert request.instructions.startswith("# 全局规则")
    assert request.model_input[0] == {"role": "user", "content": "What did we discuss?"}
    runtime_context = _runtime_context(request)
    assert runtime_context["state"] == {"service_skills": {"resident": None, "expired": []}}
    assert "delegated_agent" not in runtime_context["state"]
    assert "run_id" not in runtime_context["state"]
    assert "thread_id" not in runtime_context["state"]
    assert "actor_user_id" not in runtime_context["state"]
    assert runtime_context["user_context"] == {
        "current_time": "2026-07-08T14:30:00+08:00",
        "timezone": "Asia/Shanghai",
        "locale": "zh-CN",
        "location": {"country": "CN", "region": "Shanghai", "city": "Shanghai"},
    }
    assert runtime_context["recent_run_facts"] == []
    assert repository.routing_decisions[0]["selected_skill_id"] == "cozymate_service_agent"
    assert repository.routing_decisions[0]["routing_source"] == "passthrough"
    assert repository.routing_decisions[0]["confidence"] == 1
    assert request.model_input[-1] == {"role": "user", "content": "Summarize it."}
    assert [checkpoint["state_summary"]["node_name"] for checkpoint in checkpoint_store.checkpoints] == ["finish"]
    assert checkpoint_store.checkpoints[0]["state_summary"]["current_user_message_id"] == str(current_user.id)
    assert "context_base" in checkpoint_store.checkpoints[0]["state_summary"]["timings_ms"]
    assert "routing" in checkpoint_store.checkpoints[0]["state_summary"]["timings_ms"]
    assert "facts_projection" in checkpoint_store.checkpoints[0]["state_summary"]["timings_ms"]
    assert "model_reasoning" in checkpoint_store.checkpoints[0]["state_summary"]["timings_ms"]
    assert "total_before_finish_checkpoint" in checkpoint_store.checkpoints[0]["state_summary"]["timings_ms"]
    assert state_store.projections == []
    assert repository.run_summaries[0].payload["user_goal"] == "Summarize it."
    assert repository.run_summaries[0].payload["assistant_conclusion"] == "Here is the summary."
    assert _progress_phases(repository) == [
        "context_loading",
        "context_ready",
        "model_reasoning",
    ]
    timing_payloads = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "production_backend.agent_runtime"
        and json.loads(record.getMessage()).get("event") == "agent.run.executor_turn"
    ]
    assert timing_payloads[-1]["run_id"] == str(run.id)
    assert timing_payloads[-1]["status"] == "completed"
    assert timing_payloads[-1]["final_text_length"] == len("Here is the summary.")
    assert "model_reasoning" in timing_payloads[-1]["timings_ms"]


def test_agent_runtime_executor_projects_precomputed_memory_snapshot_into_dynamic_context() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="How should you remind me?", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    backend = CapturingSdkBackend(result=SdkNodeResult(final_text="I will keep reminders concise."))
    state_store = FakeStateStore()
    memory_service = FakeMemoryService(
        snapshot=[
            {
                "memory_type": "communication_preference",
                "summary": "Prefers concise reminders",
            }
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
    assert memory_service.calls == [{"owner_user_id": run.actor_user_id, "limit": 5}]
    assert memory_facts == [
        {
            "memory_type": "communication_preference",
            "summary": "Prefers concise reminders",
        }
    ]
    assert state_store.projections == []


def test_agent_runtime_executor_loads_base_context_without_parallel_shared_session_access() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Hello", sequence=1)
    session_guard = FakeSharedSessionGuard()
    repository = SessionGuardedRuntimeRepository(messages=[current_user], current_message=current_user, session_guard=session_guard)
    memory_service = SessionGuardedMemoryService(snapshot=[], session_guard=session_guard)
    backend = CapturingSdkBackend(result=SdkNodeResult(final_text="Hello."))

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            memory_service=memory_service,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert session_guard.calls[:4] == [
        "current_message",
        "thread_messages",
        "memory_snapshot",
        "recent_run_summaries",
    ]


def test_agent_runtime_executor_load_service_skill_returns_facts_and_records_ledger() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="今天奶量怎么样？", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text="我看了最近奶量状态。",
                tool_invocations=(scripted_tool_invocation("load_service_skill", {"service_skill_id": "milk-management"}),),
                expected_available_tools=("load_service_skill",),
            )
        ]
    )
    state_store = FakeStateStore()
    business_facts_projector = FakeBusinessFactsProjector(
        facts={"schema_version": "v1", "milk_status": {"totals": {"trend_pumped_volume_ml": 420}}}
    )

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            state_store=state_store,
            business_facts_projector=business_facts_projector,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert _runtime_context(backend.requests[0])["business_facts"] == {}
    assert business_facts_projector.calls == [
        {
            "actor_user_id": run.actor_user_id,
            "run_id": run.id,
            "service_skill_id": ServiceSkillId.MILK_MANAGEMENT,
        }
    ]
    assert state_store.projections == []
    assert repository.tool_call.tool_name == "load_service_skill"
    assert repository.tool_output.safe_output["service_skill_id"] == "milk-management"
    assert "奶量管理仅处理三类任务" in repository.tool_output.safe_output["skill"]["instructions"]
    assert "tool_scope" not in repository.tool_output.safe_output
    assert {"namespace": "milk_management", "name": "records_milk_status_read"} in repository.tool_output.safe_output[
        "recommended_tools"
    ]
    assert repository.tool_output.safe_output["business_facts"] == {
        "schema_version": "v1",
        "milk_status": {"totals": {"trend_pumped_volume_ml": 420}},
    }
    skill_loaded_events = [event for event in repository.events if event.event_type == "skill.loaded"]
    assert skill_loaded_events[0].payload["service_skill_id"] == "milk-management"
    assert "records.milk_status.read" in skill_loaded_events[0].payload["recommended_tool_contracts"]
    assert repository.run_summaries[-1].service_skill_id == "milk-management"
    assert repository.run_summaries[-1].payload["tool_facts"] == []
    assert repository.run_summaries[-1].payload["loaded_service_skills"][0]["service_skill_id"] == "milk-management"


@pytest.mark.parametrize(
    ("service_skill_id", "expected_tool_names"),
    [
        (
                "birth-prep",
                {
                    "pregnancy_plan_propose",
                "plans_plan_delete_propose",
                "plans_task_complete_propose",
                "plans_task_update_propose",
                "plans_task_delete_propose",
                "birth_plan_form_create",
                "labor_communication_card_create",
                "hospital_bag_form_create",
                "hospital_bag_card_create",
                "hospital_bag_cart_update",
                "hospital_bag_pump_recommend",
            },
        ),
        (
            "health-consultation",
            {
                "records_milk_status_read",
                "diary_entry_upsert_propose",
                "ibclc_consult_card_create",
            },
        ),
        ("emotion-support", set()),
        (
            "device-guidance",
            {
                "devices_pump_status_read",
                "devices_guidance_assets_read",
                "support_ticket_propose",
            },
        ),
    ],
)
def test_service_skill_recommendations_are_small_non_authoritative_provider_tool_hints(
    service_skill_id: str,
    expected_tool_names: set[str],
) -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="继续", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text="我来继续处理。",
                tool_invocations=(scripted_tool_invocation("load_service_skill", {"service_skill_id": service_skill_id}),),
                expected_available_tools=("load_service_skill",),
            )
        ]
    )

    asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    recommendations = repository.tool_output.safe_output["recommended_tools"]
    assert {item["name"] for item in recommendations} == expected_tool_names
    assert all(set(item) == {"namespace", "name"} for item in recommendations)


def test_default_service_skills_are_file_backed() -> None:
    registry = default_service_skill_registry()

    for service_skill_id in (
        "birth-prep",
        "milk-management",
        "health-consultation",
        "emotion-support",
        "device-guidance",
    ):
        service_skill = registry.get(service_skill_id)
        assert service_skill.service_skill_id == service_skill_id
        assert service_skill.prompt_block().strip()


def test_agent_runtime_executor_publishes_final_text_deltas_to_transient_stream() -> None:
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
    assert result.assistant_message_id is not None
    assert backend.requests[0].on_text_delta is not None
    assert transient_stream.deltas == [
        {"thread_id": thread_id, "run_id": run.id, "delta": "hel", "message_stream_id": str(result.assistant_message_id)},
        {"thread_id": thread_id, "run_id": run.id, "delta": "lo", "message_stream_id": str(result.assistant_message_id)},
    ]
    assert transient_stream.progresses == [
        {
            "thread_id": thread_id,
            "run_id": run.id,
            "phase": "context_loading",
            "label": "我已经收到你的消息啦～",
            "semantic": transient_stream.progresses[0]["semantic"],
            "dedupe_key": f"{run.id}:run.progress:progress:context_loading",
        },
        {
            "thread_id": thread_id,
            "run_id": run.id,
            "phase": "context_ready",
            "label": "我先理解一下你的需求～",
            "semantic": transient_stream.progresses[1]["semantic"],
            "dedupe_key": f"{run.id}:run.progress:progress:context_ready",
        },
        {
            "thread_id": thread_id,
            "run_id": run.id,
            "phase": "model_reasoning",
            "label": "我想一下",
            "semantic": transient_stream.progresses[2]["semantic"],
            "dedupe_key": f"{run.id}:run.progress:progress:model_reasoning",
        },
    ]
    assert all(event.event_type != "message.delta" for event in repository.events)
    assert all(event.event_type != "run.progress" for event in repository.events)
    assert [progress["phase"] for progress in transient_stream.progresses] == [
        "context_loading",
        "context_ready",
        "model_reasoning",
    ]
    assert transient_stream.progresses[0]["semantic"]["surface"] == "status_bar"
    assert transient_stream.progresses[2]["semantic"]["surface"] == "thinking_note"


def test_agent_runtime_executor_keeps_progress_transient_when_event_sink_is_configured() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Stream please", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    transient_stream = FakeTransientStream()
    event_sink = AgentEventSink(repository=repository, transient_stream=transient_stream)
    backend = CapturingSdkBackend(result=SdkNodeResult(final_text="done"))

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            event_sink=event_sink,
            transient_stream=transient_stream,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert [progress["phase"] for progress in transient_stream.progresses] == [
        "context_loading",
        "context_ready",
        "model_reasoning",
    ]
    assert all(event.event_type != "run.progress" for event in repository.events)


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
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert result.final_text == "我已经整理好了。"
    assert backend.tool_names == (
        "load_service_skill",
        "birth_plan_form_create",
        "devices_guidance_assets_read",
        "devices_pump_status_read",
        "diary_entry_upsert_propose",
        "hospital_bag_card_create",
        "hospital_bag_cart_update",
        "hospital_bag_form_create",
        "hospital_bag_pump_recommend",
        "ibclc_consult_card_create",
        "images_inspect",
        "labor_communication_card_create",
        "notifications_milk_reminder_propose",
        "plans_calendar_read",
        "plans_current_read",
        "plans_milk_plan_propose",
        "plans_plan_delete_propose",
        "plans_task_complete_propose",
        "plans_task_create_propose",
        "plans_task_delete_propose",
        "plans_task_update_propose",
        "pregnancy_plan_propose",
        "profile_read",
        "profile_update",
        "records_feeding_record_propose",
        "records_feeding_record_delete_propose",
        "records_growth_read",
        "records_growth_record_propose",
        "records_growth_record_delete_propose",
        "records_growth_record_update_propose",
        "records_milk_analysis_read",
        "records_milk_status_read",
        "records_milk_summary_read",
        "records_pumping_record_propose",
        "records_pumping_record_delete_propose",
        "support_ticket_propose",
    )
    contracts = {contract.name: contract for contract in default_tool_registry().list()}
    assert backend.tool_descriptions_by_contract == {
        contract_name: contract.description for contract_name, contract in contracts.items()
    }
    assert backend.tool_schemas_by_contract == {
        contract_name: contract.input_schema for contract_name, contract in contracts.items()
    }
    assert backend.tool_schemas["hospital_bag_card_create"]["additionalProperties"] is False
    assert backend.tool_schemas["hospital_bag_card_create"]["properties"] == {}
    assert backend.tool_schemas["hospital_bag_cart_update"]["required"] == ["action"]
    assert backend.tool_schemas["devices_guidance_assets_read"]["properties"]["limit"]["maximum"] == 20
    assert backend.tool_schemas["devices_guidance_assets_read"]["properties"]["content_type"]["type"] == "string"
    assert backend.tool_schemas["devices_pump_status_read"]["properties"]["limit"]["maximum"] == 20
    assert backend.tool_schemas["diary_entry_upsert_propose"]["required"] == ["entry_date"]
    assert backend.tool_schemas["diary_entry_upsert_propose"]["properties"]["content"]["maxLength"] == 5000
    assert backend.tool_schemas["images_inspect"]["required"] == ["image_url"]
    assert backend.tool_schemas["hospital_bag_cart_update"]["additionalProperties"] is False
    assert "groups" not in backend.tool_schemas["hospital_bag_cart_update"]["properties"]
    assert backend.tool_schemas["notifications_milk_reminder_propose"]["required"] == ["title"]
    assert backend.tool_schemas["plans_calendar_read"]["properties"]["task_date"]["maxLength"] == 20
    assert backend.tool_schemas["plans_current_read"]["properties"]["limit"]["maximum"] == 20
    assert backend.tool_schemas["plans_milk_plan_propose"]["required"] == ["title"]
    assert backend.tool_schemas["plans_plan_delete_propose"]["required"] == ["plan_id"]
    assert backend.tool_schemas["plans_task_complete_propose"]["required"] == ["task_id"]
    assert backend.tool_schemas["plans_task_complete_propose"]["properties"]["completed"]["type"] == "boolean"
    assert backend.tool_schemas["plans_task_create_propose"]["required"] == ["title"]
    assert backend.tool_schemas["plans_task_delete_propose"]["required"] == ["task_id"]
    assert backend.tool_schemas["plans_task_update_propose"]["required"] == ["task_id"]
    assert "title" not in backend.tool_schemas["pregnancy_plan_propose"]["properties"]
    assert backend.tool_schemas["profile_read"]["additionalProperties"] is False
    assert backend.tool_schemas["profile_read"]["properties"] == {}
    assert backend.tool_schemas["profile_update"]["properties"]["age"]["maximum"] == 70
    assert backend.tool_schemas["records_feeding_record_propose"]["required"] == ["feed_time", "feed_type"]
    assert backend.tool_schemas["records_feeding_record_delete_propose"]["required"] == ["record_id"]
    assert backend.tool_schemas["records_growth_read"]["properties"]["limit"]["maximum"] == 20
    assert backend.tool_schemas["records_growth_record_propose"]["required"] == ["measured_at"]
    assert backend.tool_schemas["records_growth_record_update_propose"]["required"] == ["record_id"]
    assert backend.tool_schemas["records_milk_status_read"]["properties"]["days"]["maximum"] == 30
    assert backend.tool_schemas["records_milk_analysis_read"]["properties"]["limit"]["default"] == 8
    assert backend.tool_schemas["records_milk_summary_read"]["properties"]["days"]["maximum"] == 30
    assert backend.tool_schemas["records_pumping_record_propose"]["required"] == ["pump_start_time"]
    assert backend.tool_schemas["records_pumping_record_delete_propose"]["required"] == ["record_id"]
    assert backend.tool_schemas["support_ticket_propose"]["required"] == ["issue_summary"]
    assert backend.tool_schemas["support_ticket_propose"]["additionalProperties"] is False
    assert backend.tool_search_enabled is True
    assert "milk_management" in backend.tool_namespaces
    assert backend.tool_namespaces["milk_management"]["tool_names"] == [
        "records.milk_status.read",
        "records.milk_summary.read",
        "records.milk_analysis.read",
        "records.growth.read",
        "records.feeding_record.propose",
        "records.feeding_record_delete.propose",
        "records.pumping_record.propose",
        "records.pumping_record_delete.propose",
        "records.growth_record.propose",
        "records.growth_record_update.propose",
        "records.growth_record_delete.propose",
        "plans.current.read",
        "plans.calendar.read",
        "plans.milk_plan.propose",
        "plans.task_complete.propose",
        "plans.task_create.propose",
        "notifications.milk_reminder.propose",
    ]
    assert backend.tool_namespaces["milk_management"]["deferred_tool_names"] == [
        "records.feeding_record.propose",
        "records.feeding_record_delete.propose",
        "records.pumping_record.propose",
        "records.pumping_record_delete.propose",
        "records.growth_record.propose",
        "records.growth_record_update.propose",
        "records.growth_record_delete.propose",
        "plans.milk_plan.propose",
        "plans.task_complete.propose",
        "plans.task_create.propose",
        "notifications.milk_reminder.propose",
    ]
    assert backend.tool_namespaces["device_support"]["tool_names"] == [
        "devices.pump_status.read",
        "devices.guidance_assets.read",
        "support.ticket.propose",
    ]
    assert backend.tool_namespace_by_contract["profile.read"] == ""
    assert backend.tool_namespace_by_contract["profile_update"] == ""
    assert backend.tool_namespace_by_contract["images.inspect"] == ""
    assert backend.tool_namespace_by_contract["records.milk_status.read"] == "milk_management"
    assert backend.tool_deferred_by_contract["records.milk_status.read"] is False
    assert backend.tool_deferred_by_contract["records.milk_analysis.read"] is False
    assert backend.tool_deferred_by_contract["records.growth.read"] is False
    assert backend.tool_deferred_by_contract["records.feeding_record.propose"] is True
    assert backend.tool_deferred_by_contract["plans.task_update.propose"] is True
    assert backend.tool_deferred_by_contract["support.ticket.propose"] is True
    assert tool_executor.calls[0]["actor"].user_id == run.actor_user_id
    assert tool_executor.calls[0]["run_id"] == run.id
    assert tool_executor.calls[0]["tool_name"] == "profile.read"
    assert tool_executor.calls[0]["args"] == {}


def test_agent_runtime_executor_adds_model_selected_visible_image_to_current_loop() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    image_url = "/skill-assets/device-guidance/air1/images/air1_guide_parts_components.png"
    prior_assistant = _message(
        thread_id=thread_id,
        run_id=uuid4(),
        role="assistant",
        text=f"![Air1 核心部件]({image_url})\n请对照这张图。",
        sequence=1,
    )
    current_user = _message(
        thread_id=thread_id,
        run_id=run.id,
        role="user",
        text="这张图里有什么？",
        sequence=2,
    )
    repository = FakeRuntimeRepository(messages=[prior_assistant, current_user], current_message=current_user)
    model_context = (
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Inspect the selected image."},
                {
                    "type": "input_image",
                    "image_url": "data:image/png;base64,aW1hZ2U=",
                    "detail": "low",
                },
            ],
        },
    )
    tool_executor = FakeToolExecutor(
        safe_output={"status": "image_context_ready", "image_url": image_url, "detail": "low"},
        model_context=model_context,
    )
    backend = ImageInspectingSdkBackend(image_url=image_url)

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
            tool_executor=tool_executor,
        ).execute(run=run)
    )

    assert result.final_text == "图中有四个主要部件。"
    assert backend.model_context == model_context
    assert tool_executor.calls[0]["args"] == {"image_url": image_url}
    assert tool_executor.calls[0]["trusted_args"] == {"visible_image_urls": [image_url]}


def test_agent_runtime_executor_allows_service_tool_without_skill_load() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="今天奶量怎么样？", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    tool_executor = FakeToolExecutor(safe_output={"milk_status": {"total_ml": 420}})
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text="最近奶量是 420ml。",
                tool_invocations=(scripted_tool_invocation("records.milk_status.read"),),
                expected_available_tools=("load_service_skill", "records.milk_status.read"),
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
    assert result.final_text == "最近奶量是 420ml。"
    assert tool_executor.calls[0]["tool_name"] == "records.milk_status.read"
    assert tool_executor.calls[0]["args"] == {}


def test_agent_runtime_executor_generates_quick_replies_with_finalizer() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    prior_user = _message(thread_id=thread_id, run_id=uuid4(), role="user", text="我想看看今天奶量", sequence=1)
    prior_assistant = _message(thread_id=thread_id, run_id=uuid4(), role="assistant", text="我帮你看一下。", sequence=2)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="那下一步呢？", sequence=3)
    repository = FakeRuntimeRepository(messages=[prior_user, prior_assistant, current_user], current_message=current_user)
    transient_stream = FakeTransientStream()
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text="已经整理好了。",
                text_deltas=("已经", "整理好了。"),
            )
        ]
    )
    quick_reply_backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text='{"replies":[{"text":"看今日安排"},{"text":"先不保存"},{"text":"换简单版"}]}',
            )
        ]
    )

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
            quick_reply_finalizer=QuickReplyFinalizer(sdk_runner=OpenAIAgentsSdkRunner(backend=quick_reply_backend)),
            transient_stream=transient_stream,
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert result.assistant_message_id is not None
    assert result.quick_replies == [
        {"id": "qr_1", "text": "看今日安排"},
        {"id": "qr_2", "text": "先不保存"},
        {"id": "qr_3", "text": "换简单版"},
    ]
    assert backend.requests[0].tool_names == ("load_service_skill",)
    assert quick_reply_backend.requests[0].tool_names == ()
    assert quick_reply_backend.requests[0].response_text_format == QUICK_REPLY_RESPONSE_FORMAT
    finalizer_payload = json.loads(quick_reply_backend.requests[0].model_input[0]["content"])
    assert finalizer_payload["assistant_final_text"] == "已经整理好了。"
    assert [item["text"] for item in finalizer_payload["dialogue"]] == ["我想看看今天奶量", "我帮你看一下。", "那下一步呢？"]
    assert transient_stream.deltas == [
        {"thread_id": thread_id, "run_id": run.id, "delta": "已经", "message_stream_id": str(result.assistant_message_id)},
        {"thread_id": thread_id, "run_id": run.id, "delta": "整理好了。", "message_stream_id": str(result.assistant_message_id)},
    ]
    assert "response_finalizing" not in [progress["phase"] for progress in transient_stream.progresses]


def test_agent_runtime_executor_requires_exactly_three_finalizer_quick_replies() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="那下一步呢？", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    backend = ScriptedSdkBackend([scripted_sdk_response(final_text="已经整理好了。")])
    quick_reply_backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text='{"replies":[{"text":"继续聊这个"},{"text":"给我更多细节"}]}',
            )
        ]
    )

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
            quick_reply_finalizer=QuickReplyFinalizer(sdk_runner=OpenAIAgentsSdkRunner(backend=quick_reply_backend)),
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert result.quick_replies == []


def test_agent_runtime_executor_does_not_use_quick_replies_from_final_text_json() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="给我三个下一步选项", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text=(
                    '已经整理好了。\n'
                    '{"quick_replies":[{"text":"继续聊这个"},{"text":"给我更多细节"},{"text":"换个方向"}]}'
                ),
            )
        ]
    )

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert result.final_text == "已经整理好了。"
    assert result.quick_replies == []


def test_agent_runtime_executor_suppresses_streamed_structured_json_deltas() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Read my profile", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    transient_stream = FakeTransientStream()
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text='{"profile":{"display_name":"Mai"}}',
                text_deltas=('{"profile":', '{"display_name":"Mai"}}'),
            )
        ]
    )

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
            transient_stream=transient_stream,
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert result.final_text == "我已经整理好了。"
    assert transient_stream.deltas == []


def test_agent_runtime_executor_does_not_add_fallback_quick_replies_when_model_skips_tool() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="继续聊这个", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text="我整理好了，你想继续吗？",
            )
        ]
    )

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert result.quick_replies == []


def test_agent_runtime_executor_allows_service_tool_after_skill_load() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="今天奶量怎么样？", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    tool_executor = FakeToolExecutor(safe_output={"milk_status": {"total_ml": 420}})
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text="最近奶量是 420ml。",
                tool_invocations=(
                    scripted_tool_invocation("load_service_skill", {"service_skill_id": "milk-management"}),
                    scripted_tool_invocation("records.milk_status.read", {"days": 7, "limit": 5}),
                ),
                expected_available_tools=("load_service_skill", "records.milk_status.read"),
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
    assert repository.tool_output.safe_output["service_skill_id"] == "milk-management"
    assert tool_executor.calls[0]["tool_name"] == "records.milk_status.read"
    assert tool_executor.calls[0]["args"] == {"days": 7, "limit": 5}
    assert repository.run_summaries[-1].service_skill_id == "milk-management"


def test_agent_runtime_executor_does_not_advertise_tool_search_when_runner_cannot_use_namespaces() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Read my profile", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    tool_executor = FakeToolExecutor(safe_output={"profile": {"display_name": "Mai"}})
    backend = InvokingSdkBackend()

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend, provider="minimax", use_responses=False),
            tool_executor=tool_executor,
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert backend.tool_search_enabled is False
    assert backend.tool_namespaces == {}
    assert backend.tool_namespace_by_contract["profile.read"] == ""
    assert all(defer_loading is False for defer_loading in backend.tool_deferred_by_contract.values())
    assert tool_executor.calls[0]["tool_name"] == "profile.read"


def test_agent_runtime_executor_does_not_inject_service_skill_before_model_loads_it() -> None:
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
    assert request.service_skill_id == "cozymate_service_agent"
    assert "奶量管理仅处理三类任务" not in request.instructions
    assert "待产包清单" not in request.instructions
    assert "当前已接入官方资料的型号：Air1" not in request.instructions
    state = _runtime_context(request)["state"]
    assert state == {"service_skills": {"resident": None, "expired": []}}
    assert repository.routing_decisions[0]["selected_skill_id"] == "cozymate_service_agent"
    assert repository.run_summaries[0].service_skill_id == "cozymate_service_agent"
    assert request.tool_names == ("load_service_skill",)


def test_agent_runtime_executor_projects_recent_loaded_skill_only_as_resident_context() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    previous_run_id = uuid4()
    previous_summary = AgentRunSummary(
        id=uuid4(),
        run_id=previous_run_id,
        thread_id=thread_id,
        owner_user_id=run.actor_user_id,
        service_skill_id="milk-management",
        summary_type="run_fact",
        schema_version="v1",
        payload={
            "user_goal": "昨天奶量怎么样？",
            "assistant_conclusion": "昨天总奶量偏低。",
            "loaded_service_skills": [
                {
                    "service_skill_id": "milk-management",
                    "skill_version": "v1",
                    "loaded_at": "2026-07-07T10:00:00+00:00",
                    "tool_names": ["records.milk_status.read"],
                }
            ],
        },
        source_message_ids=[],
        source_tool_call_ids=[],
        created_at=datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc),
    )
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="那今天怎么安排？", sequence=1)
    repository = FakeRuntimeRepository(
        messages=[current_user],
        current_message=current_user,
        run_summaries=[previous_summary],
    )
    backend = CapturingSdkBackend(result=SdkNodeResult(final_text="继续看奶量安排。"))

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    state = _runtime_context(backend.requests[0])["state"]
    assert result.status == "completed"
    assert backend.requests[0].service_skill_id == "cozymate_service_agent"
    assert state["service_skills"]["resident"]["service_skill_id"] == "milk-management"
    assert "last_run_id" not in json.dumps(state)
    assert "assistant_conclusion" not in json.dumps(state)
    assert "coordinator" not in state
    assert "奶量管理仅处理三类任务" not in backend.requests[0].instructions
    assert repository.run_summaries[-1].run_id == run.id
    assert repository.run_summaries[-1].service_skill_id == "cozymate_service_agent"


def test_agent_runtime_executor_injects_resident_loaded_skill_for_three_followup_turns() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    loaded_run_id = uuid4()
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="那今天怎么安排？", sequence=1)
    repository = FakeRuntimeRepository(
        messages=[current_user],
        current_message=current_user,
        run_summaries=[
            _run_summary(
                run=run,
                thread_id=thread_id,
                run_id=loaded_run_id,
                service_skill_id="milk-management",
                user_goal="昨天奶量怎么样？",
                assistant_conclusion="昨天总奶量偏低。",
                loaded_service_skill_id="milk-management",
                created_at=datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc),
            )
        ],
    )
    backend = CapturingSdkBackend(result=SdkNodeResult(final_text="继续看奶量安排。"))

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    request = backend.requests[0]
    state = _runtime_context(request)["state"]
    model_input_text = json.dumps(request.model_input, ensure_ascii=False)
    assert result.status == "completed"
    assert "奶量管理仅处理三类任务" in model_input_text
    assert "奶量管理仅处理三类任务" not in request.instructions
    skill_context = state["service_skills"]
    assert skill_context["resident"]["service_skill_id"] == "milk-management"
    assert skill_context["resident"]["instructions"]
    assert {"namespace": "milk_management", "name": "records_milk_status_read"} in skill_context["resident"]["recommended_tools"]
    assert skill_context["expired"] == []
    assert "last_loaded_run_id" not in skill_context["resident"]
    assert "loaded_at" not in skill_context["resident"]
    assert "remaining_turns" not in skill_context["resident"]
    resident_summary = repository.run_summaries[-1].payload["resident_loaded_service_skill"]
    assert resident_summary["service_skill_id"] == "milk-management"
    assert "skill" not in resident_summary


def test_agent_runtime_executor_allows_service_tool_with_resident_loaded_skill() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="继续看奶量", sequence=1)
    repository = FakeRuntimeRepository(
        messages=[current_user],
        current_message=current_user,
        run_summaries=[
            _run_summary(
                run=run,
                thread_id=thread_id,
                service_skill_id="milk-management",
                loaded_service_skill_id="milk-management",
            )
        ],
    )
    tool_executor = FakeToolExecutor(safe_output={"milk_status": {"total_ml": 420}})
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text="最近奶量是 420ml。",
                tool_invocations=(scripted_tool_invocation("records.milk_status.read", {"days": 7, "limit": 5}),),
                expected_available_tools=("load_service_skill", "records.milk_status.read"),
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
    assert tool_executor.calls[0]["tool_name"] == "records.milk_status.read"
    assert tool_executor.calls[0]["args"] == {"days": 7, "limit": 5}


def test_agent_runtime_executor_expires_resident_loaded_skill_after_three_followup_turns() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="那今天怎么安排？", sequence=1)
    repository = FakeRuntimeRepository(
        messages=[current_user],
        current_message=current_user,
        run_summaries=[
            _run_summary(
                run=run,
                thread_id=thread_id,
                service_skill_id="milk-management",
                loaded_service_skill_id="milk-management",
                created_at=datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc),
            ),
            _run_summary(run=run, thread_id=thread_id, created_at=datetime(2026, 7, 7, 11, 0, tzinfo=timezone.utc)),
            _run_summary(run=run, thread_id=thread_id, created_at=datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc)),
            _run_summary(run=run, thread_id=thread_id, created_at=datetime(2026, 7, 7, 13, 0, tzinfo=timezone.utc)),
        ],
    )
    backend = CapturingSdkBackend(result=SdkNodeResult(final_text="今天先确认目标。"))

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    state = _runtime_context(backend.requests[0])["state"]
    model_input_text = json.dumps(backend.requests[0].model_input, ensure_ascii=False)
    assert result.status == "completed"
    skill_context = state["service_skills"]
    assert skill_context["resident"] is None
    assert skill_context["expired"] == [
        {
            "service_skill_id": "milk-management",
            "instruction": (
                "milk-management was loaded before, but its SKILL.md has been removed from context. "
                "Call load_service_skill if this turn still needs that skill."
            ),
        }
    ]
    assert "奶量管理仅处理三类任务" not in model_input_text
    assert repository.run_summaries[-1].payload["expired_loaded_service_skills"][0]["service_skill_id"] == "milk-management"


def test_agent_runtime_executor_keeps_recent_loaded_skill_hint_for_default_route() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    previous_summary = AgentRunSummary(
        id=uuid4(),
        run_id=uuid4(),
        thread_id=thread_id,
        owner_user_id=run.actor_user_id,
        service_skill_id="milk-management",
        summary_type="run_fact",
        schema_version="v1",
        payload={
            "loaded_service_skills": [
                {
                    "service_skill_id": "milk-management",
                    "skill_version": "v1",
                    "loaded_at": "2026-07-07T10:00:00+00:00",
                }
            ]
        },
        source_message_ids=[],
        source_tool_call_ids=[],
        created_at=datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc),
    )
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="Summarize what we discussed.", sequence=1)
    repository = FakeRuntimeRepository(
        messages=[current_user],
        current_message=current_user,
        run_summaries=[previous_summary],
    )
    backend = CapturingSdkBackend(result=SdkNodeResult(final_text="Here is the summary."))

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert backend.requests[0].service_skill_id == "cozymate_service_agent"
    assert _runtime_context(backend.requests[0])["state"]["service_skills"]["resident"]["service_skill_id"] == "milk-management"
    assert repository.run_summaries[-1].service_skill_id == "cozymate_service_agent"


def test_agent_runtime_executor_loads_birth_prep_skill_only_when_model_calls_tool() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="帮我准备待产包和分娩沟通单", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user)
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text="我先帮你确认关键信息。",
                tool_invocations=(scripted_tool_invocation("load_service_skill", {"service_skill_id": "birth-prep"}),),
                expected_available_tools=("load_service_skill",),
            )
        ]
    )
    state_store = FakeStateStore()

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            state_store=state_store,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    request = backend.requests[0]
    loaded_skill = repository.tool_output.safe_output["skill"]
    assert result.status == "completed"
    assert request.service_skill_id == "cozymate_service_agent"
    assert "CozyMate" in request.instructions
    assert "待产包清单" not in request.instructions
    assert loaded_skill["service_skill_id"] == "birth-prep"
    assert "待产包清单" in loaded_skill["instructions"]
    assert "分娩沟通单" in loaded_skill["instructions"]
    assert "每轮最多问一个缺失字段" in loaded_skill["instructions"]
    assert "奶量管理仅处理三类任务" not in request.instructions
    assert "当前已接入官方资料的型号：Air1" not in request.instructions
    assert request.tool_names == ("load_service_skill",)
    assert state_store.projections == []
    assert repository.run_summaries[-1].service_skill_id == "birth-prep"


def test_agent_runtime_executor_projects_recent_run_facts_into_dynamic_context() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    previous_run_id = uuid4()
    previous_summary = AgentRunSummary(
        id=uuid4(),
        run_id=previous_run_id,
        thread_id=thread_id,
        owner_user_id=run.actor_user_id,
        service_skill_id="milk-management",
        summary_type="run_fact",
        schema_version="v1",
        payload={
            "user_goal": "昨天奶量怎么样？",
            "assistant_conclusion": "昨天总奶量偏低，建议今天观察补水和吸奶频率。",
            "tools_used": ["records.milk_summary.read"],
            "tool_facts": [{"tool_name": "records.milk_summary.read", "safe_output": {"total_ml": 420}}],
            "loaded_service_skills": [
                {
                    "service_skill_id": "milk-management",
                    "skill_version": "v1",
                    "loaded_at": "2026-07-07T10:00:00+00:00",
                    "tool_names": ["records.milk_summary.read"],
                }
            ],
            "verbose_unused": "x" * 3000,
        },
        source_message_ids=[],
        source_tool_call_ids=[],
        created_at=datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc),
    )
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="那今天怎么安排？", sequence=1)
    repository = FakeRuntimeRepository(
        messages=[current_user],
        current_message=current_user,
        run_summaries=[previous_summary],
    )
    backend = CapturingSdkBackend(result=SdkNodeResult(final_text="今天先看最近一次记录。"))

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    runtime_context = _runtime_context(backend.requests[0])
    recent_run_facts = runtime_context["recent_run_facts"]
    assert result.status == "completed"
    assert recent_run_facts == [
        {
            "service_skill_id": "milk-management",
            "created_at": "2026-07-07T10:00:00+00:00",
                "facts": {
                    "user_goal": "昨天奶量怎么样？",
                    "assistant_conclusion": "昨天总奶量偏低，建议今天观察补水和吸奶频率。",
                    "tool_facts": [{"tool_name": "records.milk_summary.read", "safe_output": {"total_ml": 420}}],
                },
        }
    ]
    assert runtime_context["state"]["service_skills"]["resident"]["service_skill_id"] == "milk-management"
    assert repository.run_summaries[-1].run_id == run.id
    assert repository.run_summaries[-1].service_skill_id == "cozymate_service_agent"
    assert repository.run_summaries[-1].payload["assistant_conclusion"] == "今天先看最近一次记录。"
    assert backend.requests[0].service_skill_id == "cozymate_service_agent"


def test_agent_runtime_executor_trims_recent_run_facts_already_visible_in_history() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    previous_run_id = uuid4()
    previous_user = _message(thread_id=thread_id, run_id=previous_run_id, role="user", text="昨天奶量怎么样？", sequence=1)
    previous_assistant = _message(thread_id=thread_id, run_id=previous_run_id, role="assistant", text="昨天总奶量偏低。", sequence=2)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="那今天怎么安排？", sequence=3)
    previous_summary = AgentRunSummary(
        id=uuid4(),
        run_id=previous_run_id,
        thread_id=thread_id,
        owner_user_id=run.actor_user_id,
        service_skill_id="milk-management",
        summary_type="run_fact",
        schema_version="v1",
        payload={
            "user_goal": "昨天奶量怎么样？",
            "assistant_conclusion": "昨天总奶量偏低。",
            "tools_used": ["records.milk_summary.read"],
            "tool_facts": [{"tool_name": "records.milk_summary.read", "safe_output": {"total_ml": 420}}],
            "loaded_service_skills": [
                {
                    "service_skill_id": "milk-management",
                    "skill_version": "v1",
                    "loaded_at": "2026-07-07T10:00:00+00:00",
                    "tool_names": ["records.milk_summary.read"],
                }
            ],
        },
        source_message_ids=[str(previous_user.id)],
        source_tool_call_ids=[],
        created_at=datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc),
    )
    repository = FakeRuntimeRepository(
        messages=[previous_user, previous_assistant, current_user],
        current_message=current_user,
        run_summaries=[previous_summary],
    )
    backend = CapturingSdkBackend(result=SdkNodeResult(final_text="今天先看最近一次记录。"))

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    runtime_context = _runtime_context(backend.requests[0])
    recent_run_facts = runtime_context["recent_run_facts"]
    state = runtime_context["state"]
    assert result.status == "completed"
    assert recent_run_facts == [
        {
            "service_skill_id": "milk-management",
            "created_at": "2026-07-07T10:00:00+00:00",
                "facts": {
                    "tool_facts": [{"tool_name": "records.milk_summary.read", "safe_output": {"total_ml": 420}}],
                },
        }
    ]
    assert state["service_skills"]["resident"]["service_skill_id"] == "milk-management"
    assert backend.requests[0].model_input[0] == {"role": "user", "content": "昨天奶量怎么样？"}
    assert backend.requests[0].model_input[1] == {"role": "assistant", "content": "昨天总奶量偏低。"}


def test_agent_runtime_executor_keeps_recent_assistant_conclusion_when_not_visible_in_history() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    previous_run_id = uuid4()
    previous_user = _message(thread_id=thread_id, run_id=previous_run_id, role="user", text="昨天奶量怎么样？", sequence=1)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="那今天怎么安排？", sequence=2)
    previous_summary = AgentRunSummary(
        id=uuid4(),
        run_id=previous_run_id,
        thread_id=thread_id,
        owner_user_id=run.actor_user_id,
        service_skill_id="milk-management",
        summary_type="run_fact",
        schema_version="v1",
        payload={
            "user_goal": "昨天奶量怎么样？",
            "assistant_conclusion": "昨天总奶量偏低。",
            "tools_used": ["records.milk_summary.read"],
            "tool_facts": [{"tool_name": "records.milk_summary.read", "safe_output": {"total_ml": 420}}],
            "loaded_service_skills": [
                {
                    "service_skill_id": "milk-management",
                    "skill_version": "v1",
                    "loaded_at": "2026-07-07T10:00:00+00:00",
                    "tool_names": ["records.milk_summary.read"],
                }
            ],
        },
        source_message_ids=[str(previous_user.id)],
        source_tool_call_ids=[],
        created_at=datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc),
    )
    repository = FakeRuntimeRepository(
        messages=[previous_user, current_user],
        current_message=current_user,
        run_summaries=[previous_summary],
    )
    backend = CapturingSdkBackend(result=SdkNodeResult(final_text="今天先看最近一次记录。"))

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    recent_run_facts = _runtime_context(backend.requests[0])["recent_run_facts"]
    assert result.status == "completed"
    assert recent_run_facts[0]["facts"] == {
        "assistant_conclusion": "昨天总奶量偏低。",
        "tool_facts": [{"tool_name": "records.milk_summary.read", "safe_output": {"total_ml": 420}}],
    }


def test_agent_runtime_executor_loads_device_skill_only_when_model_calls_tool() -> None:
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
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text="I will check device status.",
                tool_invocations=(scripted_tool_invocation("load_service_skill", {"service_skill_id": "device-guidance"}),),
                expected_available_tools=("load_service_skill",),
            )
        ]
    )

    asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
        ).execute(run=run)
    )

    request = backend.requests[0]
    loaded_skill = repository.tool_output.safe_output["skill"]
    assert request.service_skill_id == "cozymate_service_agent"
    assert "每轮给 1 个主步骤" not in request.instructions
    assert "Air1 (BP334)" in loaded_skill["instructions"]
    assert "每轮给 1 个主步骤" in loaded_skill["instructions"]
    assert "待产包清单" not in request.instructions
    assert "奶量管理仅处理三类任务" not in request.instructions
    assert repository.run_summaries[-1].service_skill_id == "device-guidance"


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

    history = backend.requests[0].model_input[:-2]
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
    tool_events = [event for event in repository.events if event.event_type.startswith("tool.")]
    assert [event.event_type for event in tool_events] == ["tool.started", "tool.completed"]
    assert tool_events[0].payload["label"] == "个人资料"
    assert tool_events[1].payload["label"] == "个人资料"
    assert tool_events[0].payload["semantic"]["label"] == "我先看看你的基础信息～"
    assert tool_events[1].payload["semantic"]["label"] == "我把基础信息看好啦"
    assert "model_reasoning_after_tool" in _progress_phases(repository)
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


def test_agent_runtime_executor_loads_skill_through_unified_tool_executor() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="帮我看看最近奶量", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user, run=run)
    registry = default_tool_registry()
    tool_executor = ToolExecutor(registry=registry, repository=repository, handlers={})
    backend = ServiceSkillLoadingSdkBackend(service_skill_id="milk-management")

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
            tool_registry=registry,
            tool_executor=tool_executor,
        ).execute(run=run)
    )

    assert result.status == "completed"
    assert repository.tool_call is not None
    assert repository.tool_call.tool_name == "load_service_skill"
    assert repository.tool_call.status == "completed"
    assert repository.tool_output.safe_output["service_skill_id"] == "milk-management"
    assert "instructions" not in repository.tool_output.safe_output["skill"]
    loaded_model_context = json.loads(backend.model_context[0]["content"])["runtime_loaded_service_skill"]
    assert loaded_model_context["service_skill_id"] == "milk-management"
    assert "奶量管理仅处理三类任务" in loaded_model_context["instructions"]
    assert {"namespace": "milk_management", "name": "records_milk_status_read"} in repository.tool_output.safe_output[
        "recommended_tools"
    ]
    assert [event.event_type for event in repository.events if event.event_type.startswith("tool.")] == [
        "tool.started",
        "tool.completed",
    ]
    loaded_event = next(event for event in repository.events if event.event_type == "skill.loaded")
    assert "records.milk_status.read" in loaded_event.payload["recommended_tool_contracts"]


def test_agent_runtime_executor_injects_verified_form_submission_as_non_persistent_trusted_tool_args() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(
        thread_id=thread_id,
        run_id=run.id,
        role="user",
        text="我已提交待产包信息采集表单。",
        sequence=1,
        content_overrides={
            "attachments": [
                {
                    "type": "form_submission",
                    "submission_id": str(uuid4()),
                    "artifact_id": str(uuid4()),
                    "form_id": "hospital_bag_intake",
                    "values": {"due_date_or_week": "32周", "birth_path": "顺产"},
                    "verified": True,
                }
            ]
        },
    )
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user, run=run)
    registry = default_tool_registry()
    captured_args: dict[str, Any] = {}

    async def capture_handler(context: ToolHandlerContext) -> dict[str, Any]:
        captured_args.update(context.args)
        return {"status": "card_created"}

    tool_executor = ToolExecutor(
        registry=registry,
        repository=repository,
        handlers={"hospital_bag_card_create": capture_handler},
    )
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text="待产包清单已生成。",
                tool_invocations=(scripted_tool_invocation("hospital_bag_card_create", {}),),
            )
        ]
    )

    result = asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
            tool_registry=registry,
            tool_executor=tool_executor,
        ).execute(run=run)
    )

    attachment = current_user.content["attachments"][0]
    assert result.status == "completed"
    assert repository.tool_call.safe_args == {}
    assert captured_args == {
        "confirmed_form_data": {"due_date_or_week": "32周", "birth_path": "顺产"},
        "form_submission_id": attachment["submission_id"],
    }


def test_agent_runtime_executor_prefills_form_from_runtime_business_facts_without_model_args() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="开始准备待产包", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user, run=run)
    registry = default_tool_registry()
    captured_args: dict[str, Any] = {}

    async def capture_handler(context: ToolHandlerContext) -> dict[str, Any]:
        captured_args.update(context.args)
        return {"status": "form_created"}

    tool_executor = ToolExecutor(
        registry=registry,
        repository=repository,
        handlers={"hospital_bag_form_create": capture_handler},
    )
    business_facts_projector = FakeBusinessFactsProjector(
        facts={"pregnancy": {"profile": {"delivery_date": "2026-09-18"}}}
    )
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text="信息采集表已准备好。",
                tool_invocations=(scripted_tool_invocation("hospital_bag_form_create", {}),),
            )
        ]
    )

    asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
            tool_registry=registry,
            tool_executor=tool_executor,
            business_facts_projector=business_facts_projector,
        ).execute(run=run)
    )

    assert repository.tool_call.safe_args == {}
    assert captured_args == {"default_values": {"due_date_or_week": "2026-09-18"}}
    assert business_facts_projector.calls[0]["service_skill_id"] == ServiceSkillId.BIRTH_PREP


def test_pregnancy_runtime_plan_context_ignores_active_non_pregnancy_plans() -> None:
    context = _pregnancy_runtime_plan_context(
        {
            "pregnancy": {
                "profile": {"delivery_date": "2026-09-18"},
                "plans": [
                    {"id": "milk-plan", "plan_type": "milk_management", "status": "active", "title": "追奶计划"},
                    {"id": "pregnancy-plan", "plan_type": "pregnancy", "status": "active", "title": "孕期计划"},
                ],
            }
        }
    )

    assert context == {
        "has_active_plan": True,
        "delivery_date": "2026-09-18",
        "active_plan_id": "pregnancy-plan",
        "active_plan_title": "孕期计划",
    }


def test_pregnancy_runtime_plan_context_allows_creation_when_only_other_plan_types_are_active() -> None:
    context = _pregnancy_runtime_plan_context(
        {
            "pregnancy": {
                "plans": [
                    {"id": "milk-plan", "plan_type": "milk_management", "status": "active", "title": "追奶计划"},
                ],
            }
        }
    )

    assert context == {"has_active_plan": False}


def test_agent_runtime_executor_injects_latest_cart_state_without_exposing_groups_to_model() -> None:
    thread_id = uuid4()
    run = _run(thread_id=thread_id)
    current_user = _message(thread_id=thread_id, run_id=run.id, role="user", text="把纸尿裤删掉", sequence=1)
    repository = FakeRuntimeRepository(messages=[current_user], current_message=current_user, run=run)
    repository.latest_thread_artifact = AgentArtifact(
        id=uuid4(),
        run_id=uuid4(),
        owner_user_id=run.actor_user_id,
        artifact_type="hospital_bag_cart",
        schema_version="1.0",
        status="created",
        payload={"cart_update": {"groups": [{"title": "宝宝用品", "items": [{"id": "baby-diaper", "qty": 1}]}]}},
        raw_payload_ref="",
    )
    registry = default_tool_registry()
    captured_args: dict[str, Any] = {}

    async def capture_handler(context: ToolHandlerContext) -> dict[str, Any]:
        captured_args.update(context.args)
        return {"status": "cart_updated"}

    tool_executor = ToolExecutor(
        registry=registry,
        repository=repository,
        handlers={"hospital_bag_cart_update": capture_handler},
    )
    backend = ScriptedSdkBackend(
        [
            scripted_sdk_response(
                final_text="已经移除纸尿裤。",
                tool_invocations=(
                    scripted_tool_invocation(
                        "hospital_bag_cart_update",
                        {"action": "remove_items", "item_ids": ["baby-diaper"]},
                    ),
                ),
            )
        ]
    )

    asyncio.run(
        AgentRuntimeExecutor(
            repository=repository,
            sdk_runner=OpenAIAgentsSdkRunner(backend=backend),
            tool_registry=registry,
            tool_executor=tool_executor,
        ).execute(run=run)
    )

    assert repository.tool_call.safe_args == {"action": "remove_items", "item_ids": ["baby-diaper"]}
    assert captured_args["groups"] == [{"title": "宝宝用品", "items": [{"id": "baby-diaper", "qty": 1}]}]


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
    assert _non_progress_event_types(repository) == []


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
    assert _non_progress_event_types(repository) == []


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
    assert _non_progress_event_types(repository) == []


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
    artifact_events = [event for event in repository.events if event.event_type == "artifact.created"]
    assert len(artifact_events) == 1
    assert artifact_events[0].payload["artifact_id"] == str(repository.artifacts[0].id)
    assert artifact_events[0].payload["semantic"]["surface"] == "artifact"


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


def _runtime_context(request: SdkNodeRequest) -> dict:
    return request.model_input[-2]["content"]["runtime_context"]


def _progress_phases(repository: "FakeRuntimeRepository") -> list[str]:
    return [event.payload["phase"] for event in repository.events if event.event_type == "run.progress"]


def _non_progress_event_types(repository: "FakeRuntimeRepository") -> list[str]:
    return [event.event_type for event in repository.events if event.event_type != "run.progress"]


def _run_summary(
    *,
    run: AgentRun,
    thread_id,
    run_id=None,
    service_skill_id: str = "cozymate_service_agent",
    user_goal: str = "Summarize it.",
    assistant_conclusion: str = "Summary.",
    loaded_service_skill_id: str = "",
    created_at: datetime | None = None,
) -> AgentRunSummary:
    payload: dict[str, Any] = {
        "user_goal": user_goal,
        "assistant_conclusion": assistant_conclusion,
    }
    if loaded_service_skill_id:
        payload["loaded_service_skills"] = [
            {
                "service_skill_id": loaded_service_skill_id,
                "skill_version": "v1",
                "loaded_at": (created_at or datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc)).isoformat(),
                "tool_names": ["records.milk_status.read"],
            }
        ]
    summary_created_at = created_at or datetime(2026, 7, 7, 10, 0, tzinfo=timezone.utc)
    return AgentRunSummary(
        id=uuid4(),
        run_id=run_id or uuid4(),
        thread_id=thread_id,
        owner_user_id=run.actor_user_id,
        service_skill_id=service_skill_id,
        summary_type="run_fact",
        schema_version="v1",
        payload=payload,
        source_message_ids=[],
        source_tool_call_ids=[],
        created_at=summary_created_at,
        updated_at=summary_created_at,
    )


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
        self.latest_thread_artifact = None

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
            self.run = _run(thread_id=self.current_message.thread_id, run_id=run_id)
            return self.run
        return None

    async def record_routing_decision(self, **kwargs):
        self.routing_decisions.append(kwargs)
        run = await self.get_run(run_id=kwargs["run_id"])
        if run is not None:
            run.service_skill_id = kwargs["selected_skill_id"]
            run.routing_source = kwargs["routing_source"]
            run.routing_confidence_score = int(float(kwargs["confidence"]) * 100)
            run.routing_summary = {
                "execution_mode": kwargs["execution_mode"],
                "intents": kwargs["intents"],
                "reason_codes": kwargs["reason_codes"],
                "safety_flags": kwargs["safety_flags"],
                "needs_clarification": kwargs["needs_clarification"],
                "tool_scope_version": kwargs["tool_scope_version"],
            }
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

    async def get_latest_artifact_for_thread(self, **kwargs):
        artifact = self.latest_thread_artifact
        if artifact is None:
            return None
        if artifact.owner_user_id != kwargs["owner_user_id"] or artifact.artifact_type != kwargs["artifact_type"]:
            return None
        return artifact

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


class FakeSharedSessionGuard:
    def __init__(self) -> None:
        self.active_label = ""
        self.calls = []

    async def run(self, label: str, callback):
        if self.active_label:
            raise AssertionError(f"shared session used concurrently by {self.active_label} and {label}")
        self.active_label = label
        self.calls.append(label)
        await asyncio.sleep(0)
        try:
            return await callback()
        finally:
            self.active_label = ""


class SessionGuardedRuntimeRepository(FakeRuntimeRepository):
    def __init__(self, *, session_guard: FakeSharedSessionGuard, **kwargs) -> None:
        super().__init__(**kwargs)
        self.session_guard = session_guard

    async def get_latest_user_message_for_run(self, *, run_id):
        async def load_current_message():
            return await super(SessionGuardedRuntimeRepository, self).get_latest_user_message_for_run(run_id=run_id)

        return await self.session_guard.run(
            "current_message",
            load_current_message,
        )

    async def list_messages_for_thread(self, *, thread_id, limit=40):
        async def load_thread_messages():
            return await super(SessionGuardedRuntimeRepository, self).list_messages_for_thread(thread_id=thread_id, limit=limit)

        return await self.session_guard.run(
            "thread_messages",
            load_thread_messages,
        )

    async def list_recent_run_summaries(self, *, thread_id, owner_user_id, limit, summary_type="run_fact", exclude_run_id=None):
        async def load_recent_run_summaries():
            return await super(SessionGuardedRuntimeRepository, self).list_recent_run_summaries(
                thread_id=thread_id,
                owner_user_id=owner_user_id,
                limit=limit,
                summary_type=summary_type,
                exclude_run_id=exclude_run_id,
            )

        return await self.session_guard.run(
            "recent_run_summaries",
            load_recent_run_summaries,
        )


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
    def __init__(self, *, snapshot: list[dict[str, Any]]) -> None:
        self.snapshot = snapshot
        self.calls = []

    async def get_runtime_snapshot(self, *, owner_user_id, limit=5):
        self.calls.append({"owner_user_id": owner_user_id, "limit": limit})
        return self.snapshot[:limit]


class SessionGuardedMemoryService(FakeMemoryService):
    def __init__(self, *, snapshot: list[dict[str, Any]], session_guard: FakeSharedSessionGuard) -> None:
        super().__init__(snapshot=snapshot)
        self.session_guard = session_guard

    async def get_runtime_snapshot(self, *, owner_user_id, limit=5):
        async def load_snapshot():
            return await super(SessionGuardedMemoryService, self).get_runtime_snapshot(
                owner_user_id=owner_user_id,
                limit=limit,
            )

        return await self.session_guard.run("memory_snapshot", load_snapshot)


class FakeBusinessFactsProjector:
    def __init__(self, *, facts: dict[str, Any]) -> None:
        self.facts = facts
        self.calls = []

    async def project(self, *, actor, run_id, service_skill_id):
        self.calls.append(
            {
                "actor_user_id": actor.user_id,
                "run_id": run_id,
                "service_skill_id": service_skill_id,
            }
        )
        return self.facts


class FakeTransientStream:
    def __init__(self) -> None:
        self.deltas = []
        self.progresses = []

    async def publish_message_delta(self, *, thread_id, run_id, delta, message_stream_id="assistant", ttl_seconds=600):
        self.deltas.append({"thread_id": thread_id, "run_id": run_id, "delta": delta, "message_stream_id": message_stream_id})
        return None

    async def publish_progress(
        self,
        *,
        thread_id,
        run_id,
        phase,
        label,
        semantic=None,
        dedupe_key="",
        optimistic=True,
        durable=False,
        ttl_seconds=600,
    ):
        self.progresses.append(
            {
                "thread_id": thread_id,
                "run_id": run_id,
                "phase": phase,
                "label": label,
                "semantic": semantic,
                "dedupe_key": dedupe_key,
            }
        )
        return None


class FakeToolExecutor:
    def __init__(self, *, safe_output, model_context=()):
        self.safe_output = safe_output
        self.model_context = model_context
        self.calls = []

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return FakeToolExecutionResult(safe_output=self.safe_output, model_context=self.model_context)


class FakeToolExecutionResult:
    def __init__(self, *, safe_output, model_context=()):
        self.safe_output = safe_output
        self.model_context = model_context


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
        self.tool_schemas_by_contract = {}
        self.tool_descriptions_by_contract = {}
        self.tool_namespaces = {}
        self.tool_search_enabled = False
        self.tool_namespace_by_contract = {}
        self.tool_deferred_by_contract = {}

    async def run(self, request: SdkNodeRequest) -> SdkNodeResult:
        self.tool_names = tuple(tool.sdk_name for tool in request.tools)
        self.tool_schemas = {tool.sdk_name: tool.params_json_schema for tool in request.tools}
        self.tool_schemas_by_contract = {tool.contract_name: tool.params_json_schema for tool in request.tools}
        self.tool_descriptions_by_contract = {tool.contract_name: tool.description for tool in request.tools}
        self.tool_namespaces = {
            namespace.name: {
                "tool_names": list(namespace.tool_names),
                "deferred_tool_names": list(namespace.deferred_tool_names),
            }
            for namespace in request.tool_namespaces
        }
        self.tool_search_enabled = request.tool_search_enabled
        self.tool_namespace_by_contract = {tool.contract_name: tool.namespace_name for tool in request.tools}
        self.tool_deferred_by_contract = {tool.contract_name: tool.defer_loading for tool in request.tools}
        profile_tool = next(tool for tool in request.tools if tool.contract_name == "profile.read")
        invocation = await profile_tool.invoke("{}")
        return SdkNodeResult(final_text=invocation.output_json)


class ImageInspectingSdkBackend:
    def __init__(self, *, image_url: str) -> None:
        self.image_url = image_url
        self.model_context = ()

    async def run(self, request: SdkNodeRequest) -> SdkNodeResult:
        image_tool = next(tool for tool in request.tools if tool.contract_name == "images.inspect")
        invocation = await image_tool.invoke(json.dumps({"image_url": self.image_url}))
        self.model_context = invocation.model_context
        return SdkNodeResult(final_text="图中有四个主要部件。")


class ServiceSkillLoadingSdkBackend:
    def __init__(self, *, service_skill_id: str) -> None:
        self.service_skill_id = service_skill_id
        self.model_context = ()

    async def run(self, request: SdkNodeRequest) -> SdkNodeResult:
        load_skill_tool = next(tool for tool in request.tools if tool.contract_name == "load_service_skill")
        invocation = await load_skill_tool.invoke(json.dumps({"service_skill_id": self.service_skill_id}))
        self.model_context = invocation.model_context
        return SdkNodeResult(final_text="我来看看最近奶量。")


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
