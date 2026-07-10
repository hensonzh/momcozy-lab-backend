from uuid import uuid4

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
