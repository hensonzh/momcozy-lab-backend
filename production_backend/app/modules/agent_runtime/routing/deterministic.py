from __future__ import annotations

from collections.abc import Iterable

from .schemas import IntentItem, RoutingContext, RoutingPlan, RoutingSource, SpecialistId


SURFACE_TO_SPECIALIST: dict[str, SpecialistId] = {
    "pregnancy_page": SpecialistId.PREGNANCY,
    "pregnancy_plan_page": SpecialistId.PREGNANCY,
    "hospital_bag_page": SpecialistId.PREGNANCY,
    "diary_page": SpecialistId.PREGNANCY,
    "milk_dashboard": SpecialistId.LACTATION,
    "records_page": SpecialistId.LACTATION,
    "pump_page": SpecialistId.LACTATION,
    "postpartum_page": SpecialistId.POSTPARTUM,
    "status_page": SpecialistId.POSTPARTUM,
    "device_page": SpecialistId.AFTER_SALES,
    "device_manage_page": SpecialistId.AFTER_SALES,
    "support_page": SpecialistId.AFTER_SALES,
}

SPECIALIST_KEYWORDS: dict[SpecialistId, tuple[str, ...]] = {
    SpecialistId.PREGNANCY: (
        "pregnancy",
        "pregnant",
        "due date",
        "birth plan",
        "hospital bag",
        "diary",
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
    SpecialistId.LACTATION: (
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
    SpecialistId.POSTPARTUM: (
        "postpartum",
        "recovery",
        "pelvic",
        "lochia",
        "mood",
        "产后",
        "康复",
        "恢复",
        "恶露",
        "盆底",
        "情绪",
    ),
    SpecialistId.AFTER_SALES: (
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


class DeterministicSpecialistRouter:
    def route(self, ctx: RoutingContext) -> RoutingPlan | None:
        normalized = _normalize(ctx.message)
        if ctx.pending_action_id is not None:
            return _plan(
                SpecialistId.GENERAL,
                source=RoutingSource.PENDING_ACTION,
                intent_type="pending_action_response",
                confidence=1,
                reason_codes=["pending_action"],
            )

        safety_flags = _matched_terms(normalized, SAFETY_RED_FLAGS)
        if safety_flags:
            return _plan(
                SpecialistId.SAFETY,
                source=RoutingSource.SAFETY_RULE,
                intent_type="safety_escalation",
                confidence=1,
                reason_codes=["safety_red_flag"],
                safety_flags=safety_flags,
                execution_mode="blocked_for_safety",
            )

        surface_specialist = SURFACE_TO_SPECIALIST.get(str(ctx.app_surface or ""))
        if surface_specialist is not None:
            return _plan(
                surface_specialist,
                source=RoutingSource.APP_SURFACE,
                intent_type=f"{surface_specialist.value}_surface_request",
                confidence=0.92,
                reason_codes=[f"app_surface:{ctx.app_surface}"],
            )

        if ctx.active_specialist_id is not None and not _looks_like_topic_switch(normalized, ctx.active_specialist_id):
            return _plan(
                ctx.active_specialist_id,
                source=RoutingSource.ACTIVE_WORKFLOW,
                intent_type=f"{ctx.active_specialist_id.value}_follow_up",
                confidence=0.86,
                reason_codes=["active_workflow_sticky"],
            )

        matched_specialists = _matched_specialists(normalized)
        if _looks_multi_intent(normalized) and len(matched_specialists) > 1:
            primary = matched_specialists[0]
            return RoutingPlan(
                primary_specialist_id=primary,
                intents=[
                    IntentItem(
                        intent_type=f"{specialist.value}_request",
                        specialist_id=specialist,
                        priority=30 + index * 10,
                    )
                    for index, specialist in enumerate(matched_specialists)
                ],
                execution_mode="single_with_note",
                confidence=0.74,
                source=RoutingSource.COMPLEXITY_RULE,
                reason_codes=["multi_intent_marker", "cross_specialist_intents"],
            )

        if matched_specialists:
            specialist = matched_specialists[0]
            return _plan(
                specialist,
                source=RoutingSource.KEYWORD_FAST_PATH,
                intent_type=f"{specialist.value}_request",
                confidence=0.78,
                reason_codes=["domain_keyword"],
            )

        if _looks_multi_intent(normalized):
            return None

        return None


def _plan(
    specialist_id: SpecialistId,
    *,
    source: RoutingSource,
    intent_type: str,
    confidence: float,
    reason_codes: list[str],
    safety_flags: list[str] | None = None,
    execution_mode: str = "single",
) -> RoutingPlan:
    return RoutingPlan(
        primary_specialist_id=specialist_id,
        intents=[
            IntentItem(
                intent_type=intent_type,
                specialist_id=specialist_id,
                priority=10 if specialist_id == SpecialistId.SAFETY else 50,
                safety_sensitive=specialist_id == SpecialistId.SAFETY,
            )
        ],
        execution_mode=execution_mode,  # type: ignore[arg-type]
        confidence=confidence,
        source=source,
        reason_codes=reason_codes,
        safety_flags=safety_flags or [],
    )


def _normalize(message: str) -> str:
    return " ".join(message.lower().split())


def _matched_terms(normalized: str, terms: Iterable[str]) -> list[str]:
    return [term for term in terms if term.lower() in normalized]


def _matched_specialists(normalized: str) -> list[SpecialistId]:
    matched: list[SpecialistId] = []
    for specialist, terms in SPECIALIST_KEYWORDS.items():
        if _matched_terms(normalized, terms):
            matched.append(specialist)
    return matched


def _looks_multi_intent(normalized: str) -> bool:
    return any(marker in normalized for marker in MULTI_INTENT_MARKERS)


def _looks_like_topic_switch(normalized: str, active_specialist: SpecialistId) -> bool:
    matched = _matched_specialists(normalized)
    return bool(matched and active_specialist not in matched)
