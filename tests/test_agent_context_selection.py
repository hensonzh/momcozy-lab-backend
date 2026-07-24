from types import SimpleNamespace

from app.agent_runtime.context.selection import (
    estimate_json_tokens,
    select_bounded_context_items,
)


def test_context_selection_keeps_current_message_and_bounds_old_history() -> None:
    records = [
        _record(
            sequence=index,
            item_key=f"message:{index}",
            item={"role": "user", "content": f"old message {index} " + ("x" * 80)},
        )
        for index in range(1, 31)
    ]

    selected = select_bounded_context_items(
        records,
        required_item_keys={"message:30"},
        max_items=8,
        max_estimated_tokens=150,
    )

    assert selected.items[-1]["content"].startswith("old message 30")
    assert selected.item_refs[-1]["item_key"] == "message:30"
    assert len(selected.items) <= 8
    assert selected.estimated_tokens <= 150
    assert "message:1" not in {item["item_key"] for item in selected.item_refs}


def test_context_selection_keeps_function_calls_atomic_and_drops_orphan_outputs() -> None:
    records = [
        _record(
            sequence=1,
            item_key="orphan-output",
            item={
                "type": "function_call_output",
                "call_id": "orphan",
                "output": "{}",
            },
        ),
        _record(
            sequence=2,
            item_key="call-1",
            item={"type": "function_call", "call_id": "paired", "name": "maternal_infant_profile_read"},
        ),
        _record(
            sequence=3,
            item_key="output-1",
            item={
                "type": "function_call_output",
                "call_id": "paired",
                "output": "{}",
            },
        ),
        _record(
            sequence=4,
            item_key="message:current",
            item={"role": "user", "content": "current"},
        ),
    ]

    selected = select_bounded_context_items(
        records,
        required_item_keys={"message:current"},
        max_items=4,
        max_estimated_tokens=200,
    )

    selected_keys = [item["item_key"] for item in selected.item_refs]
    assert selected_keys == ["call-1", "output-1", "message:current"]
    assert estimate_json_tokens(selected.items) == selected.estimated_tokens


def _record(*, sequence: int, item_key: str, item: dict):
    return SimpleNamespace(
        item=item,
        item_key=item_key,
        item_type=str(item.get("type") or "message"),
        sequence=sequence,
    )
