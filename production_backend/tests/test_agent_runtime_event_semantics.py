from uuid import uuid4

import pytest

from production_backend.app.modules.agent_runtime.event_semantics import (
    progress_live_dedupe_key,
    run_progress_payload,
    tool_event_semantic,
)


def test_run_progress_payload_uses_status_bar_semantic_for_visible_progress() -> None:
    payload = run_progress_payload(phase="response_finalizing", label="我在组织回复～")

    assert payload["semantic"] == {
        "phase": "replying",
        "label": "我在组织回复～",
        "surface": "status_bar",
        "visibility": "status",
        "merge_key": "progress:response_finalizing",
        "priority": 80,
        "lifecycle": "running",
    }
    assert progress_live_dedupe_key(run_id=uuid4(), semantic=payload["semantic"])


def test_run_progress_payload_aligns_context_ready_with_legacy_routing_copy() -> None:
    payload = run_progress_payload(phase="context_ready", label="我先理解一下你的需求～")

    assert payload["label"] == "我先理解一下你的需求～"
    assert payload["semantic"]["label"] == "我先理解一下你的需求～"
    assert payload["semantic"]["surface"] == "status_bar"
    assert payload["semantic"]["visibility"] == "status"


def test_run_progress_payload_keeps_model_reasoning_out_of_status_bar() -> None:
    payload = run_progress_payload(phase="model_reasoning", label="我想一下")

    assert payload["semantic"]["surface"] == "thinking_note"
    assert payload["semantic"]["visibility"] == "hidden"
    assert payload["semantic"]["label"] == "我想一下"


def test_tool_event_semantic_uses_tool_specific_copy() -> None:
    semantic = tool_event_semantic(
        event_type="tool.started",
        tool_name="records.milk_status.read",
        read_or_write="read",
    )

    assert semantic["phase"] == "reading"
    assert semantic["label"] == "我先看看今天的奶量状态～"
    assert semantic["surface"] == "status_bar"
    assert semantic["lifecycle"] == "running"


def test_image_inspect_tool_event_semantic_uses_image_copy() -> None:
    started = tool_event_semantic(
        event_type="tool.started",
        tool_name="images.inspect",
        read_or_write="read",
    )
    completed = tool_event_semantic(
        event_type="tool.completed",
        tool_name="images.inspect",
        safe_output={"status": "image_context_ready"},
        read_or_write="read",
    )

    assert started["label"] == "我先看看图片内容～"
    assert completed["label"] == "我把图片内容看好啦"


def test_tool_event_semantic_maps_confirmation_outputs() -> None:
    semantic = tool_event_semantic(
        event_type="tool.completed",
        tool_name="plans.task_create.propose",
        safe_output={"requires_confirmation": True},
        read_or_write="write",
        requires_confirmation=True,
    )

    assert semantic["phase"] == "planning"
    assert semantic["label"] == "我已经准备好预览，等你确认～"
    assert semantic["lifecycle"] == "completed"


def test_plan_tool_event_semantics_use_single_preview_and_confirmation_lifecycle() -> None:
    milk_started = tool_event_semantic(
        event_type="tool.started",
        tool_name="plans.milk_plan.propose",
        read_or_write="write",
        requires_confirmation=True,
    )
    pregnancy_completed = tool_event_semantic(
        event_type="tool.completed",
        tool_name="pregnancy.plan.propose",
        safe_output={"requires_confirmation": True},
        read_or_write="write",
        requires_confirmation=True,
    )
    existing_plan = tool_event_semantic(
        event_type="tool.completed",
        tool_name="pregnancy.plan.propose",
        safe_output={"status": "existing_plan_found"},
        read_or_write="write",
        requires_confirmation=True,
    )

    assert milk_started["label"] == "我先帮你整理奶量计划～"
    assert pregnancy_completed["label"] == "我已经准备好预览，等你确认～"
    assert existing_plan["label"] == "我找到已有的孕期计划啦"


@pytest.mark.parametrize(
    ("tool_name", "status", "forbidden_success_copy"),
    [
        ("pregnancy_diary.entry.create", "entry_already_exists", "已经保存好"),
        ("pregnancy_diary.entry.update", "entry_not_found", "已经更新好"),
        ("pregnancy_diary.entry.update", "entry_unchanged", "已经保存好"),
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
        read_or_write="write",
    )

    assert semantic["lifecycle"] == "completed"
    assert forbidden_success_copy not in semantic["label"]
