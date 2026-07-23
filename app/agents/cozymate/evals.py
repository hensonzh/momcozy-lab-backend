from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.agent_runtime.evals.service import AgentEvalSeedAssertionEngine

from .service_skills import ServiceSkillId
from .tools import default_tool_registry


COZYMATE_AGENT_ID = "cozymate_service_agent"
PRODUCT_AGENT_EVAL_SEED_SCHEMA_VERSION = "agent_eval_seed.v2"
REQUIRED_PRODUCT_AGENT_EVAL_SUITES = (
    "birth_prep",
    "hospital_bag_cart_update",
    "device_guidance",
    "device_known_guidance",
    "device_support_handoff",
    "milk_daily_summary",
    "pump_session_summary",
    "milk_schedule_management",
    "milk_plan_creation",
    "milk_trend_analysis",
    "pregnancy_plan_creation",
    "pregnancy_task_completion",
    "pregnancy_diary_entry",
    "postpartum_recovery_checkin",
    "postpartum_recovery_task",
    "memory_preference_capture",
    "memory_sensitive_rejection",
    "ibclc_consult",
    "health_consultation",
    "infant_health_red_flag",
    "emotion_support",
    "emotion_harm_baby",
    "mixed_intent_and_safety",
    "permission_bypass",
    "prompt_injection",
)
REQUIRED_PRODUCT_AGENT_EVAL_CASE_FIELDS = (
    "suite",
    "name",
    "domain",
    "input",
    "expected_behavior",
    "expected_tool_calls",
)


def create_cozymate_eval_assertion_engine() -> AgentEvalSeedAssertionEngine:
    registry = default_tool_registry()
    return AgentEvalSeedAssertionEngine(
        write_tool_names=frozenset(
            contract.name
            for contract in registry.list()
            if contract.effect_scope in {"user_resource", "external_resource"}
        ),
        wrapper_agent_id=COZYMATE_AGENT_ID,
        scene_service_skill_ids=frozenset(skill_id.value for skill_id in ServiceSkillId),
        non_write_tool_actions={},
    )


def load_product_agent_eval_seed_cases(path: Path) -> list[dict[str, Any]]:
    return validate_product_agent_eval_seed_payload(json.loads(path.read_text()))


def validate_product_agent_eval_seed_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("schema_version") != PRODUCT_AGENT_EVAL_SEED_SCHEMA_VERSION:
        raise ValueError(f"Agent eval seed schema_version must be {PRODUCT_AGENT_EVAL_SEED_SCHEMA_VERSION}.")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Agent eval seed must include a non-empty cases list.")

    normalized_cases: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"Agent eval seed case {index} must be an object.")
        missing_fields = [field for field in REQUIRED_PRODUCT_AGENT_EVAL_CASE_FIELDS if field not in case]
        if missing_fields:
            raise ValueError(f"Agent eval seed case {index} is missing fields: {', '.join(missing_fields)}.")
        suite = _require_non_empty_string(case["suite"], field_name=f"cases[{index}].suite")
        if not isinstance(case["input"], dict):
            raise ValueError(f"Agent eval seed case {index} input must be an object.")
        if not isinstance(case["expected_behavior"], dict):
            raise ValueError(f"Agent eval seed case {index} expected_behavior must be an object.")
        if not isinstance(case["expected_tool_calls"], list):
            raise ValueError(f"Agent eval seed case {index} expected_tool_calls must be a list.")
        normalized_cases.append(case | {"suite": suite})

    suites = {case["suite"] for case in normalized_cases}
    missing_suites = [suite for suite in REQUIRED_PRODUCT_AGENT_EVAL_SUITES if suite not in suites]
    if missing_suites:
        raise ValueError(f"Agent eval seed is missing required suites: {', '.join(missing_suites)}.")
    return normalized_cases


def _require_non_empty_string(value: Any, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty string.")
    return normalized
