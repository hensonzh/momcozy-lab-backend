from __future__ import annotations

from typing import Any

from .types import RuntimeInputs


HEALTH_GUIDANCE_ALLOWED_DOMAINS = [
    "www.who.int",
    "iris.who.int",
    "www.nice.org.uk",
    "www.ncbi.nlm.nih.gov",
    "www.unicef.org",
    "publications.aap.org",
    "www.uspreventiveservicestaskforce.org",
    "www.bfmed.org",
    "abm.memberclicks.net",
    "www.cdc.gov",
    "www.acog.org",
    "www.nhc.gov.cn",
    "seleguide.yiigle.com",
    "www.cmcha.org",
    "ts-cms.jundaodsj.com",
]

_HEALTH_DOMAIN_TERMS = (
    "孕期",
    "怀孕",
    "产检",
    "胎动",
    "宫缩",
    "破水",
    "阴道出血",
    "分娩",
    "产程",
    "产后",
    "坐月子",
    "母乳",
    "哺乳",
    "亲喂",
    "含乳",
    "乳房",
    "乳头",
    "涨奶",
    "堵奶",
    "乳腺",
    "吸奶",
    "泵奶",
    "储奶",
    "背奶",
    "宝宝",
    "新生儿",
    "尿布",
    "黄疸",
    "体重",
    "喂养",
)

_COMPLEX_HEALTH_TERMS = (
    "怎么办",
    "怎么处理",
    "怎么判断",
    "是否",
    "需要",
    "能不能",
    "可以吗",
    "风险",
    "指南",
    "专业",
    "依据",
    "多久",
    "持续",
    "反复",
    "越来越",
    "发热",
    "发烧",
    "寒战",
    "疼",
    "痛",
    "红肿",
    "硬块",
    "异常",
    "不舒服",
    "吃不饱",
    "不够",
    "困难",
    "减少",
    "增多",
    "清洗",
    "消毒",
    "冷藏",
    "冷冻",
)

_NON_HEALTH_PRODUCT_TERMS = (
    "待产包",
    "入院包",
    "住院包",
    "清单",
    "购物车",
    "买什么",
    "买哪些",
    "生产计划",
    "全过程计划",
    "分娩沟通单",
)

_URGENT_RED_FLAG_TERMS = (
    "胎动明显减少",
    "胎动减少",
    "大出血",
    "阴道出血",
    "破水",
    "胸痛",
    "晕厥",
    "昏厥",
    "呼吸困难",
    "高烧",
    "寒战",
    "剧烈疼痛",
    "严重头痛",
    "视物模糊",
    "宝宝呼吸",
    "肤色发紫",
)


def should_include_health_guidance_context(inputs: RuntimeInputs) -> bool:
    message = str(inputs.get("user_message") or "").strip()
    if not message or "confirmed_form_data:" in message:
        return False
    if any(term in message for term in _NON_HEALTH_PRODUCT_TERMS):
        return False
    if any(term in message for term in _URGENT_RED_FLAG_TERMS):
        return False

    has_health_domain = any(term in message for term in _HEALTH_DOMAIN_TERMS)
    has_complex_signal = any(term in message for term in _COMPLEX_HEALTH_TERMS)

    return has_health_domain and has_complex_signal


def health_guidance_web_search_tool() -> dict[str, Any]:
    return {
        "type": "web_search",
        "filters": {
            "allowed_domains": HEALTH_GUIDANCE_ALLOWED_DOMAINS,
        },
    }


def health_guidance_required_web_search_tool_choice(inputs: RuntimeInputs) -> dict[str, Any] | None:
    if not should_include_health_guidance_context(inputs):
        return None
    return {
        "type": "allowed_tools",
        "mode": "required",
        "tools": [{"type": "web_search"}],
    }


def health_guidance_request_context_lines(inputs: RuntimeInputs) -> list[str]:
    if not should_include_health_guidance_context(inputs):
        return []
    return [
        "health_guidance_context:",
        "- 本轮问题像是相对复杂的母婴健康咨询；已提供 Responses API web_search，且限制在专业资料 allowlist 域名内。",
        "- 回答前优先使用 web_search 检索 WHO、NICE、ACOG、ABM、CDC、AAP、国家卫健委等来源；不要基于记忆硬答复杂健康判断。",
        "- 明显急症或红旗信号先给医生/急救分流，不为了检索延迟安全提醒。",
        "- 最终回复保持 CoMate 风格：短、自然、不诊断；只概括和当前问题最相关的依据与下一步。",
    ]
