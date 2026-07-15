from __future__ import annotations

from dataclasses import dataclass


SAFETY_RESPONSE_TEMPLATE_VERSION = "safety-response.v2"


@dataclass(frozen=True)
class SafetyResponseTemplate:
    key: str
    version: str
    category: str
    handoff_type: str
    display_intent: str
    response_text: str


SAFETY_RESPONSE_TEMPLATES: dict[str, SafetyResponseTemplate] = {
    "emotional_crisis_escalation": SafetyResponseTemplate(
        key="emotional_crisis_escalation",
        version=SAFETY_RESPONSE_TEMPLATE_VERSION,
        category="emotional_crisis",
        handoff_type="crisis_support",
        display_intent="Escalate to crisis or emergency support and stop normal product flows.",
        response_text=(
            "听起来你现在可能正处在非常危险、难熬的时刻。请立即远离可能造成伤害的物品，"
            "不要独处，并联系一位你信任的人陪着你。如果你或他人可能马上受到伤害，请立即联系当地急救或报警服务；"
            "在中国大陆可拨打 120 或 110。本回复不能替代紧急援助。"
        ),
    ),
    "maternal_infant_health_escalation": SafetyResponseTemplate(
        key="maternal_infant_health_escalation",
        version=SAFETY_RESPONSE_TEMPLATE_VERSION,
        category="health_red_flag",
        handoff_type="medical_or_emergency_support",
        display_intent="Escalate maternal or infant health red flags without diagnosis.",
        response_text=(
            "你描述的情况可能涉及妈妈或宝宝的紧急健康风险。请立即停止在线咨询，联系当地急救服务或前往最近的急诊；"
            "在中国大陆可拨打 120。若可以，请让身边的人陪同，并把症状开始时间、变化和已经使用的药物告诉医护人员。"
            "本回复不能替代现场医疗评估。"
        ),
    ),
    "security_refusal": SafetyResponseTemplate(
        key="security_refusal",
        version=SAFETY_RESPONSE_TEMPLATE_VERSION,
        category="prompt_injection",
        handoff_type="none",
        display_intent="Refuse prompt-injection or hidden-instruction extraction attempts.",
        response_text="我不能提供系统提示词、隐藏指令或内部安全配置，但可以继续帮助你处理母婴相关的问题。",
    ),
    "none": SafetyResponseTemplate(
        key="none",
        version=SAFETY_RESPONSE_TEMPLATE_VERSION,
        category="none",
        handoff_type="none",
        display_intent="No safety response template is needed.",
        response_text="",
    ),
}


def get_safety_response_template(key: str) -> SafetyResponseTemplate:
    return SAFETY_RESPONSE_TEMPLATES[key]
