from pathlib import Path

import pytest

from production_backend.app.modules.agent_runtime.evals import (
    REQUIRED_PRODUCT_AGENT_EVAL_SUITES,
    load_product_agent_eval_seed_cases,
    validate_product_agent_eval_seed_payload,
)


ROOT = Path(__file__).resolve().parents[2]
PRODUCT_AGENT_EVAL_SEED = ROOT / "production_backend" / "fixtures" / "agent_eval_cases" / "product_service_seed.json"


def test_product_agent_eval_seed_covers_required_suites() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)

    suites = {case["suite"] for case in cases}

    assert set(REQUIRED_PRODUCT_AGENT_EVAL_SUITES) <= suites
    assert len(cases) >= len(REQUIRED_PRODUCT_AGENT_EVAL_SUITES)


def test_product_agent_eval_seed_cases_have_action_and_safety_contracts() -> None:
    cases = load_product_agent_eval_seed_cases(PRODUCT_AGENT_EVAL_SEED)

    for case in cases:
        behavior = case["expected_behavior"]
        assert behavior["intent"] == case["suite"]
        assert isinstance(behavior["route"], str) and behavior["route"]
        assert isinstance(behavior["requires_confirmation_before_write"], bool)
        assert case["expected_safety_decision"] in {"allow", "escalate", "block"}
        assert "messages" in case["input"]
        assert isinstance(case["expected_tool_calls"], list)


def test_product_agent_eval_seed_loader_rejects_missing_required_suite() -> None:
    payload = {
        "schema_version": "agent_eval_seed.v1",
        "cases": [
            {
                "suite": "birth_prep",
                "name": "incomplete seed",
                "domain": "birth_prep",
                "input": {"messages": []},
                "expected_behavior": {"intent": "birth_prep", "route": "structured_service", "requires_confirmation_before_write": True},
                "expected_tool_calls": [],
                "expected_safety_decision": "allow",
            }
        ],
    }

    with pytest.raises(ValueError, match="missing required suites"):
        validate_product_agent_eval_seed_payload(payload)
