from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from .models import AgentEvalCase
from .repository import AgentRuntimeRepository
from .replay import AgentReplayService


class AgentEvalService:
    def __init__(self, *, repository: AgentRuntimeRepository, replay_service: AgentReplayService | None = None) -> None:
        self.repository = repository
        self.replay_service = replay_service or AgentReplayService(repository=repository)

    async def create_case_from_run(
        self,
        *,
        run_id: UUID,
        suite: str,
        name: str,
        domain: str = "",
        owner_team: str = "",
    ) -> AgentEvalCase:
        bundle = await self.replay_service.export_run_bundle(run_id=run_id, include_message_content=False)
        return await self.repository.create_eval_case(
            suite=suite,
            name=name,
            domain=domain,
            input_payload={"replay_bundle": bundle},
            expected_behavior={
                "final_run_status": bundle["run"]["status"],
                "event_types": [event["type"] for event in bundle["events"]],
                "action_statuses": [action["status"] for action in bundle["actions"]],
            },
            expected_tool_calls=[{"tool_name": tool_call["tool_name"], "status": tool_call["status"]} for tool_call in bundle["tool_calls"]],
            expected_safety_decision=_last_safety_decision(bundle),
            source_run_id=run_id,
            status="draft",
            owner_team=owner_team,
        )


PRODUCT_AGENT_EVAL_SEED_SCHEMA_VERSION = "agent_eval_seed.v1"
REQUIRED_PRODUCT_AGENT_EVAL_SUITES = (
    "birth_prep",
    "labor_communication",
    "device_guidance",
    "device_support_handoff",
    "milk_daily_summary",
    "pump_session_summary",
    "milk_schedule_management",
    "milk_plan_creation",
    "milk_trend_analysis",
    "pregnancy_plan_creation",
    "pregnancy_task_completion",
    "pregnancy_diary_entry",
    "memory_preference_capture",
    "ibclc_consult",
    "health_consultation",
    "emotion_support",
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
    "expected_safety_decision",
)
ALLOWED_PRODUCT_AGENT_SAFETY_DECISIONS = frozenset({"allow", "escalate", "block"})


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
        safety_decision = _require_non_empty_string(
            case["expected_safety_decision"],
            field_name=f"cases[{index}].expected_safety_decision",
        )
        if safety_decision not in ALLOWED_PRODUCT_AGENT_SAFETY_DECISIONS:
            raise ValueError(f"Agent eval seed case {index} has unsupported expected_safety_decision.")
        if not isinstance(case["input"], dict):
            raise ValueError(f"Agent eval seed case {index} input must be an object.")
        if not isinstance(case["expected_behavior"], dict):
            raise ValueError(f"Agent eval seed case {index} expected_behavior must be an object.")
        if not isinstance(case["expected_tool_calls"], list):
            raise ValueError(f"Agent eval seed case {index} expected_tool_calls must be a list.")
        normalized_cases.append(case | {"suite": suite, "expected_safety_decision": safety_decision})

    suites = {case["suite"] for case in normalized_cases}
    missing_suites = [suite for suite in REQUIRED_PRODUCT_AGENT_EVAL_SUITES if suite not in suites]
    if missing_suites:
        raise ValueError(f"Agent eval seed is missing required suites: {', '.join(missing_suites)}.")
    return normalized_cases


def _last_safety_decision(bundle: dict[str, Any]) -> str:
    safety_events = bundle.get("safety_events")
    if not isinstance(safety_events, list) or not safety_events:
        return ""
    last = safety_events[-1]
    if not isinstance(last, dict):
        return ""
    return str(last.get("decision") or "")


def _require_non_empty_string(value: Any, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty string.")
    return normalized
