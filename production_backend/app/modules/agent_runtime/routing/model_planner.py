from __future__ import annotations

import json
from typing import Any

from ....core.errors import ApiError
from ..prompts import build_service_skill_planner_prompt
from ..sdk import OpenAIAgentsSdkRunner, SdkNodeRequest
from ..skill_registry import AgentServiceSkillRegistry, default_service_skill_registry
from ..tools.groups import ToolGroupRegistry, default_tool_group_registry
from .schemas import IntentItem, RoutingContext, RoutingPlan, RoutingSource, ServiceSkillId
from .service import SkillIntentPlanner


DEFAULT_TOOL_GROUPS: dict[ServiceSkillId, tuple[str, ...]] = {
    ServiceSkillId.GENERAL: ("general.base",),
    ServiceSkillId.PREGNANCY: ("general.base", "pregnancy.context"),
    ServiceSkillId.LACTATION: ("general.base", "lactation.milk_read"),
    ServiceSkillId.POSTPARTUM: ("general.base", "postpartum.context"),
    ServiceSkillId.AFTER_SALES: ("general.base", "after_sales.device_guidance"),
    ServiceSkillId.SAFETY: ("general.base", "safety.support"),
}


class ModelSkillIntentPlanner(SkillIntentPlanner):
    """Use a small model call to select the current service skill and visible tool groups."""

    def __init__(
        self,
        *,
        sdk_runner: OpenAIAgentsSdkRunner,
        skill_registry: AgentServiceSkillRegistry | None = None,
        tool_group_registry: ToolGroupRegistry | None = None,
    ) -> None:
        self.sdk_runner = sdk_runner
        self.skill_registry = skill_registry or default_service_skill_registry()
        self.tool_group_registry = tool_group_registry or default_tool_group_registry()

    async def classify(self, ctx: RoutingContext) -> RoutingPlan:
        try:
            result = await self.sdk_runner.run_reasoning(
                SdkNodeRequest(
                    run_id=str(ctx.run_id),
                    thread_id=str(ctx.thread_id),
                    actor_user_id=str(ctx.actor_user_id),
                    instructions=_planner_instructions(
                        skill_registry=self.skill_registry,
                        tool_group_registry=self.tool_group_registry,
                    ),
                    model_input=[{"role": "user", "content": _planner_user_payload(ctx)}],
                    tool_names=(),
                    tools=(),
                    prompt_version="momcozy-service-skill-planner-v1",
                    trace_id=f"{ctx.run_id}:service-skill-planner",
                    service_skill_id="service_skill_planner",
                )
            )
        except ApiError as exc:
            return _fallback_plan(reason_codes=[f"model_planner_failed:{exc.code}"], confidence=0.4)

        return _plan_from_model_text(
            text=result.final_text,
            skill_registry=self.skill_registry,
            tool_group_registry=self.tool_group_registry,
        )


def _planner_instructions(
    *,
    skill_registry: AgentServiceSkillRegistry,
    tool_group_registry: ToolGroupRegistry,
) -> str:
    skills = [
        {
            "service_skill_id": skill.service_skill_id,
            "name": skill.name,
            "description": skill.description,
            "scope": list(skill.scope),
            "deliverables": list(skill.deliverables),
        }
        for skill in skill_registry.list()
    ]
    tool_groups = [
        {
            "id": group.id,
            "service_skill_id": group.service_skill_id.value if group.service_skill_id is not None else None,
            "description": group.description,
        }
        for group in tool_group_registry.list()
    ]
    manifest = json.dumps({"skills": skills, "tool_groups": tool_groups}, ensure_ascii=False, sort_keys=True)
    return build_service_skill_planner_prompt(candidate_manifest=manifest)


def _planner_user_payload(ctx: RoutingContext) -> dict[str, Any]:
    return {
        "message": ctx.message,
        "app_surface": ctx.app_surface,
        "active_service_skill_id": ctx.active_service_skill_id.value if ctx.active_service_skill_id is not None else None,
        "active_workflow": ctx.active_workflow,
        "has_pending_action": ctx.pending_action_id is not None,
        "attachment_types": ctx.attachment_types,
    }


def _plan_from_model_text(
    *,
    text: str,
    skill_registry: AgentServiceSkillRegistry,
    tool_group_registry: ToolGroupRegistry,
) -> RoutingPlan:
    raw = _extract_json_object(text)
    if raw is None:
        return _fallback_plan(reason_codes=["model_planner_invalid_json"], confidence=0.4)

    skill_id = _service_skill_id(raw.get("service_skill_id"))
    if skill_id is None:
        return _fallback_plan(reason_codes=["model_planner_invalid_service_skill"], confidence=_confidence(raw))
    try:
        skill_registry.get(skill_id.value)
    except KeyError:
        return _fallback_plan(reason_codes=["model_planner_unregistered_service_skill"], confidence=_confidence(raw))

    reason_codes = _string_list(raw.get("reason_codes")) or ["model_planner"]
    tool_group_ids = _sanitize_tool_group_ids(
        selected_skill_id=skill_id,
        raw_tool_group_ids=_string_list(raw.get("tool_group_ids")),
        tool_group_registry=tool_group_registry,
    )
    return RoutingPlan(
        selected_skill_id=skill_id,
        intents=[
            IntentItem(
                intent_type=_text(raw.get("intent_type")) or f"{skill_id.value}_request",
                service_skill_id=skill_id,
                priority=10 if skill_id == ServiceSkillId.SAFETY else 50,
                safety_sensitive=skill_id == ServiceSkillId.SAFETY,
            )
        ],
        tool_group_ids=list(tool_group_ids),
        execution_mode="blocked_for_safety" if skill_id == ServiceSkillId.SAFETY else "single",
        confidence=_confidence(raw),
        source=RoutingSource.MODEL_PLANNER,
        reason_codes=reason_codes,
        safety_flags=["model_planner_safety"] if skill_id == ServiceSkillId.SAFETY else [],
        needs_clarification=bool(raw.get("needs_clarification") or False),
    )


def _fallback_plan(*, reason_codes: list[str], confidence: float) -> RoutingPlan:
    return RoutingPlan(
        selected_skill_id=ServiceSkillId.GENERAL,
        intents=[IntentItem(intent_type="general_request", service_skill_id=ServiceSkillId.GENERAL)],
        tool_group_ids=["general.base"],
        execution_mode="single",
        confidence=confidence,
        source=RoutingSource.MODEL_PLANNER,
        reason_codes=reason_codes,
    )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    stripped = text.strip()
    candidates = [stripped]
    if "```" in stripped:
        candidates.extend(part.strip().removeprefix("json").strip() for part in stripped.split("```"))
    first_brace = stripped.find("{")
    if first_brace >= 0:
        candidates.append(stripped[first_brace:])

    for candidate in candidates:
        if not candidate:
            continue
        try:
            value, _ = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _service_skill_id(value: object) -> ServiceSkillId | None:
    try:
        return ServiceSkillId(str(value))
    except ValueError:
        return None


def _sanitize_tool_group_ids(
    *,
    selected_skill_id: ServiceSkillId,
    raw_tool_group_ids: list[str],
    tool_group_registry: ToolGroupRegistry,
) -> tuple[str, ...]:
    selected: list[str] = []
    for group_id in raw_tool_group_ids:
        try:
            group = tool_group_registry.get(group_id)
        except ApiError:
            continue
        if group.service_skill_id is not None and group.service_skill_id != selected_skill_id:
            continue
        if group.id not in selected:
            selected.append(group.id)
    for group_id in DEFAULT_TOOL_GROUPS[selected_skill_id]:
        if group_id not in selected:
            selected.insert(0 if group_id == "general.base" else len(selected), group_id)
    return tuple(selected)


def _confidence(raw: dict[str, Any]) -> float:
    value = raw.get("confidence", 0.55)
    if isinstance(value, int | float):
        return max(0, min(1, float(value)))
    return 0.55


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_text(item) for item in value) if item]


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
