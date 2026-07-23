from uuid import uuid4

import pytest

from app.agents.cozymate.event_semantics import (
    artifact_event_payload_semantic,
    tool_event_semantic,
)
from app.agent_runtime.events.semantics import (
    action_event_payload_semantic,
    progress_live_dedupe_key,
    run_event_payload_semantic,
    run_progress_payload,
)
from app.agents.cozymate.tools.registry import default_tool_registry


def test_run_progress_payload_uses_status_bar_semantic_for_visible_progress() -> None:
    payload = run_progress_payload(phase="response_finalizing", label="我在组织回复～")

    assert payload["semantic"] == {
        "phase": "replying",
        "label": "我在组织回复～",
        "surface": "status_bar",
        "merge_key": "progress:response_finalizing",
        "priority": 80,
        "lifecycle": "running",
    }
    assert progress_live_dedupe_key(run_id=uuid4(), semantic=payload["semantic"])


def test_run_progress_payload_aligns_context_ready_with_canonical_surface() -> None:
    payload = run_progress_payload(phase="context_ready", label="我先理解一下你的需求～")

    assert payload["label"] == "我先理解一下你的需求～"
    assert payload["semantic"]["label"] == "我先理解一下你的需求～"
    assert payload["semantic"]["surface"] == "status_bar"


def test_run_progress_payload_keeps_model_reasoning_out_of_status_bar() -> None:
    payload = run_progress_payload(phase="model_reasoning", label="我想一下")

    assert payload["semantic"]["surface"] == "thinking_note"
    assert payload["semantic"]["label"] == "我想一下"


def test_after_tool_progress_preserves_status_and_thinking_layers() -> None:
    followup = run_progress_payload(phase="model_followup", label="我接着处理下一步")
    reasoning = run_progress_payload(phase="model_reasoning_after_tool", label="我想一下")

    assert followup["semantic"] == {
        "phase": "thinking",
        "label": "我接着处理下一步",
        "surface": "status_bar",
        "merge_key": "progress:model_followup",
        "priority": 55,
        "lifecycle": "running",
    }
    assert reasoning["semantic"]["surface"] == "thinking_note"
    assert reasoning["semantic"]["label"] == "我想一下"


def test_tool_event_semantic_uses_tool_specific_copy() -> None:
    semantic = tool_event_semantic(
        event_type="tool.started",
        tool_name="records_milk_status_read",
        effect_scope="none",
    )

    assert semantic["phase"] == "reading"
    assert semantic["label"] == "我先看看今天的奶量状态～"
    assert semantic["surface"] == "work_item"
    assert semantic["lifecycle"] == "running"


def test_tool_event_semantic_uses_one_merge_key_for_the_whole_call() -> None:
    started = tool_event_semantic(
        event_type="tool.started",
        tool_name="records_milk_status_read",
        tool_call_id="call-1",
        effect_scope="none",
    )
    completed = tool_event_semantic(
        event_type="tool.completed",
        tool_name="records_milk_status_read",
        tool_call_id="call-1",
        safe_output={"status": "completed"},
        effect_scope="none",
    )

    assert started["merge_key"] == "tool:call-1"
    assert completed["merge_key"] == "tool:call-1"


def test_every_registered_tool_has_specific_started_copy() -> None:
    registry = default_tool_registry()

    for contract in registry.list():
        semantic = tool_event_semantic(
            event_type="tool.started",
            tool_name=contract.name,
            safe_args={},
            effect_scope=contract.effect_scope,
        )

        assert semantic["label"] not in {"我按当前场景继续处理～", "我先看看相关信息～", "我先准备相关信息～"}, contract.name


@pytest.mark.parametrize(
    ("tool_name", "effect_scope", "started_label", "status", "completed_label"),
    [
        ("pregnancy_diary_query", "none", "我先看看孕期日记～", "entries_read", "我看好孕期日记啦"),
        ("pregnancy_diary_save", "user_resource", "我先帮你保存孕期日记～", "entry_saved", "我已经保存好孕期日记啦"),
        ("pregnancy_diary_delete", "user_resource", "我先帮你删除孕期日记～", "entry_deleted", "我已经删除这条孕期日记啦"),
    ],
)
def test_pregnancy_diary_semantics_follow_action_and_result(
    tool_name: str,
    effect_scope: str,
    started_label: str,
    status: str,
    completed_label: str,
) -> None:
    started = tool_event_semantic(
        event_type="tool.started",
        tool_name=tool_name,
        effect_scope=effect_scope,
    )
    completed = tool_event_semantic(
        event_type="tool.completed",
        tool_name=tool_name,
        safe_output={"status": status},
        effect_scope=effect_scope,
    )

    assert started["label"] == started_label
    assert completed["label"] == completed_label


def test_conversation_history_image_load_event_semantic_uses_history_image_copy() -> None:
    started = tool_event_semantic(
        event_type="tool.started",
        tool_name="conversation_history_image_load",
        effect_scope="none",
    )
    completed = tool_event_semantic(
        event_type="tool.completed",
        tool_name="conversation_history_image_load",
        safe_output={"status": "image_context_ready"},
        effect_scope="none",
    )

    assert started["label"] == "我回看一下之前的图片～"
    assert completed["label"] == "我看清之前那张图片啦"


def test_tool_event_semantic_maps_confirmation_outputs() -> None:
    semantic = tool_event_semantic(
        event_type="tool.completed",
        tool_name="plans_task_create_propose",
        safe_output={"requires_confirmation": True},
        effect_scope="user_resource",
    )

    assert semantic["phase"] == "planning"
    assert semantic["label"] == "我已经准备好预览，等你确认～"
    assert semantic["lifecycle"] == "completed"


def test_plan_tool_event_semantics_distinguish_preview_from_applied_pregnancy_plan() -> None:
    milk_started = tool_event_semantic(
        event_type="tool.started",
        tool_name="plans_milk_plan_propose",
        effect_scope="user_resource",
    )
    pregnancy_completed = tool_event_semantic(
        event_type="tool.completed",
        tool_name="pregnancy_plan_workflow",
        safe_output={"status": "card_created"},
        effect_scope="user_resource",
    )
    existing_plan = tool_event_semantic(
        event_type="tool.completed",
        tool_name="pregnancy_plan_workflow",
        safe_output={"status": "existing_plan_found"},
        effect_scope="user_resource",
    )

    assert milk_started["label"] == "我先帮你整理奶量计划～"
    assert pregnancy_completed["label"] == "孕期计划已生成"
    assert existing_plan["label"] == "我找到已有的孕期计划啦"


@pytest.mark.parametrize(
    ("tool_name", "status", "forbidden_success_copy"),
    [
        ("pregnancy_diary_save", "action_failed", "已经保存好"),
        ("pregnancy_diary_query", "entry_not_found", "已经更新好"),
    ],
)
def test_pregnancy_diary_no_op_completion_does_not_claim_write_success(
    tool_name: str,
    status: str,
    forbidden_success_copy: str,
) -> None:
    semantic = tool_event_semantic(
        event_type="tool.completed",
        tool_name=tool_name,
        safe_output={"status": status},
        effect_scope="user_resource",
    )

    assert semantic["lifecycle"] == "completed"
    assert forbidden_success_copy not in semantic["label"]


def test_artifact_and_confirmation_semantics_use_canonical_visible_surfaces() -> None:
    artifact = artifact_event_payload_semantic(artifact_type="hospital_bag_card", artifact_id="artifact-1")
    action = action_event_payload_semantic(action_status="pending", action_id="action-1")

    assert artifact["label"] == "我已经帮你生成好待产包清单啦"
    assert artifact["surface"] == "artifact"
    assert artifact["merge_key"] == "artifact:artifact-1"
    assert action["label"] == "我需要你确认一下，再继续处理"
    assert action["surface"] == "action"
    assert action["merge_key"] == "action:action-1"


def test_run_events_carry_canonical_terminal_semantics() -> None:
    started = run_event_payload_semantic(event_type="run.started", run_id="run-1")
    failed = run_event_payload_semantic(event_type="run.failed", run_id="run-1")
    completed = run_event_payload_semantic(event_type="run.completed", run_id="run-1")

    assert started["label"] == "我已经收到你的消息啦～"
    assert failed["label"] == "这轮暂时没处理好"
    assert completed["label"] == "我处理好啦"
