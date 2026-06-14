from __future__ import annotations

import re
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
    "月子",
    "恶露",
    "伤口",
    "剖腹产",
    "剖宫产",
    "会阴",
    "侧切",
    "母乳",
    "哺乳",
    "亲喂",
    "含乳",
    "乳汁",
    "奶水",
    "奶量",
    "乳汁电导率",
    "电导率",
    "乳房",
    "乳头",
    "乳晕",
    "涨奶",
    "堵奶",
    "堵",
    "结块",
    "硬块",
    "乳腺",
    "乳腺炎",
    "乳房炎",
    "乳管",
    "吸奶",
    "泵奶",
    "储奶",
    "背奶",
    "宝宝",
    "新生儿",
    "婴儿",
    "新生宝宝",
    "尿布",
    "尿不湿",
    "大便",
    "便便",
    "吐奶",
    "呕吐",
    "黄疸",
    "脐带",
    "脐部",
    "湿疹",
    "体重",
    "喂养",
    "用药",
    "药",
    "疫苗",
    "补剂",
)

_BREAST_TRIAGE_FIRST_TERMS = (
    "硬块",
    "堵奶",
    "吸奶痛",
    "乳房疼",
    "乳房痛",
    "乳头疼",
    "乳头痛",
)

_BREAST_TRIAGE_ANSWER_TERMS = (
    "发烧",
    "发热",
    "寒战",
    "红肿",
    "一片红",
    "发红",
    "红热",
    "热痛",
    "越来越",
    "加重",
    "变大",
    "扩大",
    "没有发",
    "没发",
    "不发",
    "不红",
    "不热",
    "无发",
    "否认",
)

_COMPLEX_HEALTH_TERMS = (
    "怎么办",
    "怎么处理",
    "怎么判断",
    "怎么回事",
    "什么原因",
    "为什么",
    "是否",
    "需要",
    "要不要",
    "需不需要",
    "该不该",
    "能不能",
    "可以吗",
    "正常吗",
    "有没有事",
    "严重吗",
    "要紧吗",
    "会不会",
    "影响",
    "处理",
    "缓解",
    "观察",
    "复测",
    "风险",
    "指南",
    "专业",
    "依据",
    "多久",
    "连续",
    "持续",
    "反复",
    "越来越",
    "升高",
    "偏高",
    "偏低",
    "下降",
    "变差",
    "变少",
    "变多",
    "发热",
    "发烧",
    "寒战",
    "疼",
    "痛",
    "红",
    "热",
    "肿",
    "痒",
    "破皮",
    "出血",
    "渗液",
    "化脓",
    "硬",
    "胀",
    "刺痛",
    "酸痛",
    "红肿",
    "硬块",
    "异常",
    "指标",
    "数值",
    "检查",
    "报告",
    "不舒服",
    "吃不饱",
    "不够",
    "困难",
    "减少",
    "增多",
    "用药",
    "药",
    "疫苗",
    "补剂",
    "副作用",
    "过敏",
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


def should_include_health_guidance_context(
    inputs: RuntimeInputs,
    loaded_skill_ids: list[str] | None = None,
) -> bool:
    message = str(inputs.get("user_message") or "").strip()
    if not message or "confirmed_form_data:" in message:
        return False
    if any(term in message for term in _NON_HEALTH_PRODUCT_TERMS):
        return False
    if any(term in message for term in _URGENT_RED_FLAG_TERMS):
        return False
    if _is_milk_management_fullness_followup(message, loaded_skill_ids):
        return False

    has_health_domain = any(term in message for term in _HEALTH_DOMAIN_TERMS)
    has_complex_signal = any(term in message for term in _COMPLEX_HEALTH_TERMS)

    return has_health_domain and has_complex_signal


def _is_milk_management_fullness_followup(message: str, loaded_skill_ids: list[str] | None) -> bool:
    if not _has_loaded_skill(loaded_skill_ids, "milk-management"):
        return False
    has_fullness_signal = any(
        term in message
        for term in (
            "胀",
            "涨",
            "排不空",
            "没排空",
            "没有排空",
            "吸完还胀",
            "吸完还涨",
        )
    )
    if not has_fullness_signal:
        return False
    return not _has_unnegated_fullness_red_flag(message)


def _has_unnegated_fullness_red_flag(message: str) -> bool:
    for term in (
        "发热",
        "发烧",
        "寒战",
        "红肿",
        "发红",
        "红热",
        "硬块",
        "越来越痛",
        "疼痛加重",
        "变大",
        "扩大",
        "破皮",
        "出血",
        "化脓",
    ):
        if _contains_unnegated_term(message, term):
            return True
    return False


def _contains_unnegated_term(message: str, term: str) -> bool:
    if term not in message:
        return False
    denied_pattern = rf"(没有|没|无|不|否认)[^，。；;、\n]{{0,8}}{re.escape(term)}"
    return re.search(denied_pattern, message) is None


def _has_loaded_skill(loaded_skill_ids: list[str] | None, skill_id: str) -> bool:
    if not loaded_skill_ids:
        return False
    normalized = {str(value).strip().replace("_", "-") for value in loaded_skill_ids}
    return skill_id in normalized


def needs_breast_triage_first(inputs: RuntimeInputs) -> bool:
    message = str(inputs.get("user_message") or "").strip()
    if not message or "confirmed_form_data:" in message:
        return False
    has_breast_triage_signal = any(term in message for term in _BREAST_TRIAGE_FIRST_TERMS)
    has_triage_answer = any(term in message for term in _BREAST_TRIAGE_ANSWER_TERMS)
    return has_breast_triage_signal and not has_triage_answer


def health_guidance_web_search_tool() -> dict[str, Any]:
    return {
        "type": "web_search",
        "filters": {
            "allowed_domains": HEALTH_GUIDANCE_ALLOWED_DOMAINS,
        },
    }


def health_guidance_required_web_search_tool_choice(
    inputs: RuntimeInputs,
    loaded_skill_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    if not should_include_health_guidance_context(inputs, loaded_skill_ids):
        return None
    if needs_breast_triage_first(inputs):
        return None
    return {
        "type": "allowed_tools",
        "mode": "required",
        "tools": [{"type": "web_search"}],
    }


def health_guidance_request_context_lines(
    inputs: RuntimeInputs,
    loaded_skill_ids: list[str] | None = None,
) -> list[str]:
    if not should_include_health_guidance_context(inputs, loaded_skill_ids):
        return []
    lines = [
        "health_guidance_context:",
    ]
    if needs_breast_triage_first(inputs):
        lines.append("- 本轮像是乳房硬块/疼痛的第一步；还没确认几个要紧情况，先问一句关键问题，不需要 web_search。")
    else:
        lines.extend(
            [
                "- 本轮问题像是相对复杂的母婴健康咨询；已提供 Responses API web_search，且限制在专业资料 allowlist 域名内。",
                "- 回答前优先使用 web_search 检索 WHO、NICE、ACOG、ABM、CDC、AAP、国家卫健委等来源；不要基于记忆硬答复杂健康判断。",
                "- 普通健康咨询的目标是先帮用户完成当前可执行的一步：解释可能方向、给低风险处理/观察/记录建议；不要把医生、儿科、药师或 IBCLC 当成默认结论。",
            ]
        )
    lines.extend(
        [
            "- 只有明显在变严重、持续不缓解、涉及处方/检查/治疗判断，或妈妈/宝宝状态不对时，才建议尽快找线下医疗渠道；不为了检索延迟这类提醒。",
            "- 用户说乳房硬块、硬块疼、乳房红肿、堵奶或吸奶痛时，第一轮先确认几个要紧情况，不要直接给完整处理方案。推荐问法：我想先确认一下，你现在有没有发烧、寒战，或者乳房有一片红、热、痛？硬块有没有越来越大或越来越痛？",
            "- 如果用户本轮只是说硬块/堵奶/乳房疼痛，尚未回答发烧、寒战、红热痛、硬块是否加重这些问题，最终回复只做一句承接 + 关键问题；不要输出冷敷、按摩、排乳、用药、资料引用或 IBCLC 入口推荐。",
            "- 如果用户确认有发烧、寒战、红肿热痛扩大、疼痛明显加重或宝宝精神/吃奶/尿布异常，先承接难受，再简短说明这类情况需要尽快线下确认；不要展开复杂居家方案。",
            "- 如果用户否认发烧、寒战、红热痛扩大或疼痛加重，但仍有硬块疼、吸奶痛、反复堵奶、排乳不顺或不知道怎么喂/吸，先给低风险处理和观察建议，再问 1-3 个关键问题；当问题进入含乳、排乳、泵奶节奏、反复堵奶、宝宝摄入细节或用户反复尝试无效时，要主动引导 IBCLC 在线咨询并询问是否现在打开入口；不要未确认就创建咨询卡。",
            "- 最终回复保持 CoMate 风格：短、自然、不诊断；只概括和当前问题最相关的依据与下一步。",
        ]
    )
    return lines
