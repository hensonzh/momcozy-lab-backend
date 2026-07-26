from __future__ import annotations

import json
from pathlib import Path

from app.agents.cozymate.tools import default_tool_registry


ROOT = Path(__file__).resolve().parents[1]
QUICK_REPLY_RELEVANCE_SEED = (
    ROOT / "fixtures" / "agent_eval_cases" / "quick_reply_relevance_seed.json"
)
GENERIC_REPLIES = {"继续聊聊", "详细说说", "还有别的吗"}


def test_quick_reply_relevance_seed_covers_core_service_outcomes() -> None:
    payload = json.loads(QUICK_REPLY_RELEVANCE_SEED.read_text(encoding="utf-8"))
    model_tool_names = set(default_tool_registry().names_for_sdk())

    assert payload["schema_version"] == "quick_reply_relevance.v1"
    cases = payload["cases"]
    assert {case["scenario"] for case in cases} == {
        "milk_analysis",
        "pregnancy_plan",
        "hospital_bag",
        "device_unboxing",
        "support_handoff",
    }

    for case in cases:
        current_turn = case["input"]["current_turn"]
        turn_outcome = case["input"]["turn_outcome"]
        replies = case["reference_replies"]

        assert current_turn["user_message"].strip()
        assert current_turn["assistant_final_text"].strip()
        assert set(turn_outcome) == {"tools", "artifacts", "active_workflow"}
        assert all(tool["name"] in model_tool_names for tool in turn_outcome["tools"])
        assert len(replies) == 3
        assert len(set(replies)) == 3
        assert not GENERIC_REPLIES.intersection(replies)
        assert all(reply.strip() and len(reply) <= 32 for reply in replies)
