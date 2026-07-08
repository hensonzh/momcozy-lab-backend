from __future__ import annotations

from collections.abc import Iterable

from .schemas import IntentItem, RoutingContext, RoutingPlan, RoutingSource, ServiceSkillId


SURFACE_TO_SKILL: dict[str, tuple[ServiceSkillId, tuple[str, ...]]] = {
    "pregnancy_page": (ServiceSkillId.BIRTH_PREP, ("general.base", "pregnancy.context", "pregnancy.plan")),
    "pregnancy_plan_page": (ServiceSkillId.BIRTH_PREP, ("general.base", "pregnancy.context", "pregnancy.plan")),
    "hospital_bag_page": (ServiceSkillId.BIRTH_PREP, ("general.base", "pregnancy.context", "pregnancy.hospital_bag")),
    "diary_page": (ServiceSkillId.BIRTH_PREP, ("general.base", "pregnancy.context", "pregnancy.diary")),
    "milk_dashboard": (ServiceSkillId.MILK_MANAGEMENT, ("general.base", "lactation.milk_read")),
    "records_page": (ServiceSkillId.MILK_MANAGEMENT, ("general.base", "lactation.milk_read", "lactation.record_write")),
    "pump_page": (ServiceSkillId.MILK_MANAGEMENT, ("general.base", "lactation.milk_read")),
    "postpartum_page": (ServiceSkillId.HEALTH_CONSULTATION, ("general.base", "postpartum.context")),
    "status_page": (ServiceSkillId.HEALTH_CONSULTATION, ("general.base", "postpartum.context", "postpartum.checkin")),
    "device_page": (ServiceSkillId.DEVICE_GUIDANCE, ("general.base", "after_sales.device_guidance")),
    "device_manage_page": (ServiceSkillId.DEVICE_GUIDANCE, ("general.base", "after_sales.device_guidance")),
    "support_page": (ServiceSkillId.DEVICE_GUIDANCE, ("general.base", "after_sales.support")),
}

SKILL_HINT_TERMS: dict[ServiceSkillId, tuple[str, ...]] = {
    ServiceSkillId.BIRTH_PREP: (
        "pregnancy",
        "pregnant",
        "due date",
        "birth plan",
        "hospital bag",
        "孕",
        "预产期",
        "待产包",
        "入院包",
        "住院包",
        "日记",
        "分娩",
        "产检",
        "生产偏好",
        "产房沟通",
        "分娩沟通单",
        "给护士",
        "陪产",
    ),
    ServiceSkillId.MILK_MANAGEMENT: (
        "milk",
        "feeding",
        "feed",
        "pumping",
        "pumped",
        "pump session",
        "supply",
        "奶量",
        "喂养",
        "吸奶",
        "泵奶",
        "母乳",
        "涨奶",
        "堵奶",
        "追奶",
        "稳奶",
        "减奶",
        "含乳",
        "乳头疼",
        "ibclc",
        "哺乳顾问",
        "泌乳顾问",
    ),
    ServiceSkillId.HEALTH_CONSULTATION: (
        "health",
        "doctor",
        "symptom",
        "medicine",
        "pain",
        "fever",
        "健康",
        "医生",
        "症状",
        "用药",
        "疼",
        "发烧",
        "发热",
        "postpartum",
        "recovery",
        "pelvic",
        "lochia",
        "产后",
        "康复",
        "恢复",
        "恶露",
        "盆底",
    ),
    ServiceSkillId.EMOTION_SUPPORT: (
        "anxious",
        "anxiety",
        "sad",
        "cry",
        "stress",
        "焦虑",
        "崩溃",
        "难过",
        "想哭",
        "压力",
        "情绪",
        "害怕",
        "无助",
    ),
    ServiceSkillId.DEVICE_GUIDANCE: (
        "device",
        "air1",
        "suction",
        "bluetooth",
        "ble",
        "troubleshoot",
        "charging",
        "firmware",
        "support",
        "ticket",
        "customer service",
        "设备",
        "蓝牙",
        "故障",
        "充电",
        "吸奶器",
        "客服",
        "工单",
        "售后",
        "缺件",
        "破损",
        "保修",
        "退货",
        "换货",
        "烧焦",
        "冒烟",
    ),
}

SAFETY_RED_FLAGS: tuple[str, ...] = (
    "suicide",
    "kill myself",
    "hurt myself",
    "hurt my baby",
    "heavy bleeding",
    "cannot breathe",
    "baby turning blue",
    "自杀",
    "不想活",
    "伤害自己",
    "伤害宝宝",
    "大出血",
    "呼吸困难",
    "宝宝发紫",
    "宝宝嘴唇发紫",
    "宝宝尿布很少",
    "尿布很少",
    "没有尿布",
    "没精神",
    "嗜睡叫不醒",
    "高烧",
    "发烧",
    "发热",
    "胸口很痛",
    "胎动明显减少",
    "破水",
    "大量出血",
)

MULTI_INTENT_MARKERS: tuple[str, ...] = (
    "顺便",
    "另外",
    "同时",
    "再帮我",
    "然后",
    "以及",
    "and also",
    "also",
    "then",
)


class DeterministicSkillSignalRouter:
    """应用侧硬边界和低成本信号；普通语义判断应交给模型规划器。"""

    def route(self, ctx: RoutingContext) -> RoutingPlan | None:
        normalized = _normalize(ctx.message)
        if ctx.pending_action_id is not None:
            return _plan(
                ServiceSkillId.MAIN_AGENT,
                source=RoutingSource.PENDING_ACTION,
                intent_type="pending_action_response",
                confidence=1,
                reason_codes=["pending_action"],
                tool_group_ids=("general.base",),
            )

        safety_flags = _matched_terms(normalized, SAFETY_RED_FLAGS)
        if safety_flags:
            skill_id = _safety_skill_for_flags(safety_flags)
            return _plan(
                skill_id,
                source=RoutingSource.SAFETY_RULE,
                intent_type="safety_escalation",
                confidence=1,
                reason_codes=["safety_red_flag"],
                safety_flags=safety_flags,
                execution_mode="blocked_for_safety",
                tool_group_ids=("general.base", "safety.support"),
            )

        surface_skill = SURFACE_TO_SKILL.get(str(ctx.app_surface or ""))
        if surface_skill is not None:
            skill_id, tool_group_ids = surface_skill
            return _plan(
                skill_id,
                source=RoutingSource.APP_SURFACE,
                intent_type=f"{skill_id.value}_surface_request",
                confidence=0.92,
                reason_codes=[f"app_surface:{ctx.app_surface}"],
                tool_group_ids=tool_group_ids,
            )

        if ctx.active_service_skill_id is not None and not _looks_like_topic_switch(normalized, ctx.active_service_skill_id):
            return _plan(
                ctx.active_service_skill_id,
                source=RoutingSource.ACTIVE_WORKFLOW,
                intent_type=f"{ctx.active_service_skill_id.value}_follow_up",
                confidence=0.86,
                reason_codes=["active_skill_sticky"],
                tool_group_ids=_tool_groups_for_message(skill_id=ctx.active_service_skill_id, normalized=normalized),
            )

        matched_skills = _matched_skills(normalized)
        if _looks_multi_intent(normalized) and len(matched_skills) > 1:
            primary = matched_skills[0]
            return RoutingPlan(
                selected_skill_id=primary,
                intents=[
                    IntentItem(
                        intent_type=f"{skill_id.value}_request",
                        service_skill_id=skill_id,
                        priority=30 + index * 10,
                    )
                    for index, skill_id in enumerate(matched_skills)
                ],
                tool_group_ids=list(_tool_groups_for_message(skill_id=primary, normalized=normalized)),
                execution_mode="single_with_note",
                confidence=0.74,
                source=RoutingSource.COMPLEXITY_RULE,
                reason_codes=["multi_intent_marker", "cross_service_skill_intents"],
            )

        if matched_skills:
            skill_id = matched_skills[0]
            return _plan(
                skill_id,
                source=RoutingSource.LOCAL_HINT,
                intent_type=f"{skill_id.value}_request",
                confidence=0.72,
                reason_codes=["local_domain_hint"],
                tool_group_ids=_tool_groups_for_message(skill_id=skill_id, normalized=normalized),
            )

        if _looks_multi_intent(normalized):
            return None

        return None


def _plan(
    skill_id: ServiceSkillId,
    *,
    source: RoutingSource,
    intent_type: str,
    confidence: float,
    reason_codes: list[str],
    tool_group_ids: tuple[str, ...],
    safety_flags: list[str] | None = None,
    execution_mode: str = "single",
) -> RoutingPlan:
    return RoutingPlan(
        selected_skill_id=skill_id,
        intents=[
            IntentItem(
                intent_type=intent_type,
                service_skill_id=skill_id,
                priority=10 if safety_flags else 50,
                safety_sensitive=bool(safety_flags),
            )
        ],
        tool_group_ids=list(tool_group_ids),
        execution_mode=execution_mode,  # type: ignore[arg-type]
        confidence=confidence,
        source=source,
        reason_codes=reason_codes,
        safety_flags=safety_flags or [],
    )


def _tool_groups_for_message(*, skill_id: ServiceSkillId, normalized: str) -> tuple[str, ...]:
    if skill_id == ServiceSkillId.BIRTH_PREP:
        groups = ["general.base", "pregnancy.context"]
        if _matched_terms(normalized, ("待产包", "入院包", "住院包", "hospital bag")):
            groups.append("pregnancy.hospital_bag")
        if _matched_terms(normalized, ("分娩沟通", "生产偏好", "birth plan", "labor")):
            groups.append("pregnancy.labor_communication")
        if _matched_terms(normalized, ("计划", "任务", "todo", "安排")):
            groups.append("pregnancy.plan")
        if _matched_terms(normalized, ("日记", "记录今天", "diary")):
            groups.append("pregnancy.diary")
        return tuple(groups)
    if skill_id == ServiceSkillId.MILK_MANAGEMENT:
        groups = ["general.base", "lactation.milk_read"]
        if _matched_terms(normalized, ("补录", "记录", "保存", "add record", "log")):
            groups.append("lactation.record_write")
        if _matched_terms(normalized, ("计划", "追奶", "稳奶", "减奶", "提醒", "plan", "reminder")):
            groups.append("lactation.plan")
        if _matched_terms(normalized, ("ibclc", "哺乳顾问", "泌乳顾问", "顾问")):
            groups.append("lactation.handoff")
        return tuple(groups)
    if skill_id == ServiceSkillId.HEALTH_CONSULTATION:
        groups = ["general.base", "postpartum.context"]
        if _matched_terms(normalized, ("打卡", "状态", "check in", "check-in")):
            groups.append("postpartum.checkin")
        if _matched_terms(normalized, ("任务", "提醒", "计划", "日记")):
            groups.append("postpartum.task")
        return tuple(groups)
    if skill_id == ServiceSkillId.EMOTION_SUPPORT:
        return ("general.base", "safety.support")
    if skill_id == ServiceSkillId.DEVICE_GUIDANCE:
        groups = ["general.base", "after_sales.device_guidance"]
        if _matched_terms(normalized, ("客服", "工单", "售后", "缺件", "破损", "保修", "退货", "换货", "support", "ticket")):
            groups.append("after_sales.support")
        return tuple(groups)
    return ("general.base",)


def _normalize(message: str) -> str:
    return " ".join(message.lower().split())


def _matched_terms(normalized: str, terms: Iterable[str]) -> list[str]:
    return [term for term in terms if term.lower() in normalized]


def _matched_skills(normalized: str) -> list[ServiceSkillId]:
    matched: list[ServiceSkillId] = []
    for skill_id, terms in SKILL_HINT_TERMS.items():
        if _matched_terms(normalized, terms):
            matched.append(skill_id)
    return matched


def _looks_multi_intent(normalized: str) -> bool:
    return any(marker in normalized for marker in MULTI_INTENT_MARKERS)


def _looks_like_topic_switch(normalized: str, active_skill_id: ServiceSkillId) -> bool:
    matched = _matched_skills(normalized)
    return bool(matched and active_skill_id not in matched)


def _safety_skill_for_flags(flags: list[str]) -> ServiceSkillId:
    emotion_flags = {
        "suicide",
        "kill myself",
        "hurt myself",
        "hurt my baby",
        "自杀",
        "不想活",
        "伤害自己",
        "伤害宝宝",
    }
    if any(flag in emotion_flags for flag in flags):
        return ServiceSkillId.EMOTION_SUPPORT
    return ServiceSkillId.HEALTH_CONSULTATION
