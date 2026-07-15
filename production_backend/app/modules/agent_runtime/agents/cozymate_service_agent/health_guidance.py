from __future__ import annotations

import re


HEALTH_GUIDANCE_ALLOWED_DOMAINS = (
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
)

COMPLEX_HEALTH_SEARCH_UNAVAILABLE_RESPONSE = (
    "专业资料检索暂时不可用，我不想只凭记忆回答这个较复杂的健康问题。"
    "如果症状持续、加重，或妈妈/宝宝状态不对，请尽快联系医生；"
    "如有大量出血、胸痛、呼吸困难、晕厥，或宝宝呼吸困难、嘴唇发紫、嗜睡叫不醒等情况，请立即就医或呼叫急救。"
)

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

_REQUIRED_HEALTH_EVIDENCE_TERMS = (
    "用药",
    "药",
    "疫苗",
    "补剂",
    "黄疸",
    "电导率",
    "检查",
    "报告",
    "指标",
    "数值",
    "处方",
    "治疗",
    "指南",
    "专业",
    "依据",
)

_NON_HEALTH_PRODUCT_TERMS = (
    "待产包",
    "入院包",
    "住院包",
    "清单",
    "购物车",
    "买什么",
    "买哪些",
    "孕期计划",
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


def should_use_complex_health_web_search(message: str, loaded_skill_ids: list[str] | None = None) -> bool:
    normalized = str(message or "").strip()
    if not normalized or "confirmed_form_data:" in normalized:
        return False
    if _is_background_milk_analysis_reminder_followup(normalized):
        return False
    if any(term in normalized for term in _NON_HEALTH_PRODUCT_TERMS):
        return False
    if _has_unnegated_urgent_red_flag(normalized):
        return False
    if _is_milk_management_intake_field_question(normalized, loaded_skill_ids):
        return False
    return any(term in normalized for term in _HEALTH_DOMAIN_TERMS) and any(
        term in normalized for term in _COMPLEX_HEALTH_TERMS
    )


def should_require_complex_health_web_search(message: str, loaded_skill_ids: list[str] | None = None) -> bool:
    normalized = str(message or "").strip()
    return should_use_complex_health_web_search(normalized, loaded_skill_ids) and any(
        term in normalized for term in _REQUIRED_HEALTH_EVIDENCE_TERMS
    )


def needs_breast_triage_first(message: str) -> bool:
    normalized = str(message or "").strip()
    if not normalized or "confirmed_form_data:" in normalized:
        return False
    return any(term in normalized for term in _BREAST_TRIAGE_FIRST_TERMS) and not any(
        term in normalized for term in _BREAST_TRIAGE_ANSWER_TERMS
    )


def health_guidance_request_context_lines(
    message: str,
    loaded_skill_ids: list[str] | None = None,
) -> list[str]:
    if not should_use_complex_health_web_search(message, loaded_skill_ids):
        return []
    lines = ["health_guidance_context:"]
    if needs_breast_triage_first(message):
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
            "- 急症信号例外：大量出血、破水、胎动明显减少、胸痛、呼吸困难、晕厥，或宝宝呼吸困难/嘴唇发紫/嗜睡叫不醒/抽搐等，立即建议线下医疗或急救，不等多轮。",
            "- 用户说乳房硬块、硬块疼、乳房红肿、堵奶或吸奶痛时，第一轮先确认几个要紧情况，不要直接给完整处理方案。推荐问法：我想先确认一下，你现在有没有发烧、寒战，或者乳房有一片红、热、痛？硬块有没有越来越大或越来越痛？",
            "- 如果用户本轮只是说硬块/堵奶/乳房疼痛，尚未回答发烧、寒战、红热痛、硬块是否加重这些问题，最终回复只做一句承接 + 关键问题；不要输出冷敷、按摩、排乳、用药、资料引用或 IBCLC 入口推荐。",
            "- 用户回答上一轮健康分流问题时，例如说没有发烧、没有红肿、宝宝精神还可以，即使顺手写入孕期日记，也不能只回复已记录；要继续推进健康咨询。",
            "- 普通乳房痛、堵奶、奶量、含乳、宝宝吃没吃饱这类问题，不要在信息没了解完整前推荐去医院或 IBCLC；通常第 1 轮问安全问题，第 2 轮给低风险建议并补问，第 3 轮左右或关键问题已回答后再分流。",
            "- 如果用户确认有发烧、寒战、红肿热痛扩大、疼痛明显加重或宝宝精神/吃奶/尿布异常，先承接难受，再简短说明这类情况需要尽快线下确认；不要展开复杂居家方案。",
            "- 如果用户否认发烧、寒战、红热痛扩大或疼痛加重，但仍有硬块疼、吸奶痛、反复堵奶、排乳不顺或不知道怎么喂/吸，先给低风险处理和观察建议，再问 1-3 个关键问题；当问题进入含乳、排乳、泵奶节奏、反复堵奶、宝宝摄入细节或用户反复尝试无效时，要主动引导 IBCLC 在线咨询；不要未确认就创建咨询卡。",
            "- 最终回复保持 CoMate 风格：短、自然、不诊断；只概括和当前问题最相关的依据与下一步。",
        ]
    )
    return lines


def _is_background_milk_analysis_reminder_followup(message: str) -> bool:
    return "后台奶量分析提醒后的自动接续" in message and "奶量分析上下文" in message


def _is_milk_management_intake_field_question(message: str, loaded_skill_ids: list[str] | None) -> bool:
    if not _has_loaded_skill(loaded_skill_ids, "milk-management"):
        return False
    has_question_signal = any(
        term in message
        for term in ("为什么", "怎么判断", "怎么影响", "有什么影响", "有没有必要", "为什么要问", "为什么要看", "为啥", "啥意思")
    )
    if not has_question_signal:
        return False
    return any(
        term in message
        for term in (
            "尿布",
            "尿量",
            "精神",
            "吃奶后",
            "体重",
            "增长",
            "记录完整",
            "漏记",
            "发热",
            "寒战",
            "红肿",
            "硬块",
            "疼痛加重",
            "乳房舒适",
            "排不空",
        )
    )


def _has_loaded_skill(loaded_skill_ids: list[str] | None, skill_id: str) -> bool:
    normalized = {str(value).strip().replace("_", "-") for value in loaded_skill_ids or []}
    return skill_id in normalized


_NEGATED_TRIAGE_LIST = re.compile(
    r"(?:没有|没|无|否认)(?:发烧|发热|寒战|红肿|红热|热痛)"
    r"(?:(?:[、,，]|也|和|或|以及)+(?:(?:没有|没|无|否认))?(?:发烧|发热|寒战|红肿|红热|热痛))*"
)
_DIRECT_NEGATION = re.compile(r"(?:没有|没|无|否认|不)(?:出现|伴有)?\s*$")


def _has_unnegated_urgent_red_flag(message: str) -> bool:
    negated_spans = [match.span() for match in _NEGATED_TRIAGE_LIST.finditer(message)]
    for term in _URGENT_RED_FLAG_TERMS:
        for match in re.finditer(re.escape(term), message):
            if any(start <= match.start() and match.end() <= end for start, end in negated_spans):
                continue
            if _DIRECT_NEGATION.search(message[max(0, match.start() - 12) : match.start()]):
                continue
            return True
    return False
