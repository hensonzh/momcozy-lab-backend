from __future__ import annotations

import json
from typing import Any

from ....core.errors import ApiError
from ..sdk import OpenAIAgentsSdkRunner, SdkNodeRequest
from ..skill_registry import AgentServiceSkillRegistry, default_service_skill_registry
from ..tools.groups import ToolGroupRegistry, default_tool_group_registry
from .schemas import IntentItem, RoutingContext, RoutingPlan, RoutingSource, ServiceSkillId
from .service import SkillIntentPlanner


DEFAULT_TOOL_GROUPS: dict[ServiceSkillId, tuple[str, ...]] = {
    ServiceSkillId.MAIN_AGENT: ("general.base",),
    ServiceSkillId.BIRTH_PREP: ("general.base", "pregnancy.context"),
    ServiceSkillId.MILK_MANAGEMENT: ("general.base", "lactation.milk_read"),
    ServiceSkillId.HEALTH_CONSULTATION: ("general.base", "postpartum.context"),
    ServiceSkillId.EMOTION_SUPPORT: ("general.base", "safety.support"),
    ServiceSkillId.DEVICE_GUIDANCE: ("general.base", "after_sales.device_guidance"),
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
    return (
        "你是 MomCozy 智能体运行时的服务技能规划器，只做路由决策，不回答用户问题。\n"
        "根据用户本轮输入、页面入口、活跃流程和附件类型，从候选服务技能中选择一个主服务技能，并选择本轮需要暴露的最小工具组。\n"
        "确定性安全、待确认动作和页面入口已在应用侧优先处理；你主要处理纯自然语言和轻微多意图场景。\n"
        "如果用户同时提出多个场景，选择当前最需要推进的主场景，并在 reason_codes 中标记 multi_intent；不要返回多个主技能。\n"
        "只返回 JSON，不要输出解释、Markdown 或代码块。JSON 字段固定为："
        "service_skill_id、intent_type、tool_group_ids、confidence、needs_clarification、reason_codes。\n"
        "service_skill_id 必须来自候选服务技能；tool_group_ids 必须来自候选工具组，并且只选择属于该服务技能或通用的工具组。\n"
        "候选清单：\n"
        f"{manifest}"
    )


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
                priority=50,
                safety_sensitive=False,
            )
        ],
        tool_group_ids=list(tool_group_ids),
        execution_mode="single",
        confidence=_confidence(raw),
        source=RoutingSource.MODEL_PLANNER,
        reason_codes=reason_codes,
        safety_flags=[],
        needs_clarification=bool(raw.get("needs_clarification") or False),
    )


def _fallback_plan(*, reason_codes: list[str], confidence: float) -> RoutingPlan:
    return RoutingPlan(
        selected_skill_id=ServiceSkillId.MAIN_AGENT,
        intents=[IntentItem(intent_type="general_request", service_skill_id=ServiceSkillId.MAIN_AGENT)],
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
