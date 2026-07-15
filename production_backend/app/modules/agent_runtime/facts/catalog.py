from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable

from .types import FactInput


FACT_CATALOG_VERSION = "birth-prep-facts-v1"
PREGNANCY_PLAN_FORM_ID = "birth_journey_basic_info_intake"
HOSPITAL_BAG_FORM_ID = "hospital_bag_intake"
BIRTH_PLAN_FORM_ID = "birth_plan_card_intake"


@dataclass(frozen=True)
class FactField:
    key: str
    value_type: str
    description: str
    enum_values: tuple[str, ...] = ()
    max_length: int = 1000
    sensitivity: str = "personal"
    candidate_eligible: bool = False
    candidate_ttl_days: int = 180


FACT_FIELDS: tuple[FactField, ...] = (
    FactField("profile.age", "integer", "当前用户年龄，单位为周岁", candidate_eligible=True),
    FactField(
        "pregnancy.due_date_or_week",
        "string",
        "当前孕周或预产期",
        sensitivity="health_context",
        candidate_eligible=True,
        candidate_ttl_days=14,
    ),
    FactField(
        "pregnancy.ivf",
        "enum",
        "本次妊娠是否通过 IVF",
        ("是", "否", "不确定/暂不说"),
        sensitivity="restricted_health",
        candidate_eligible=False,
    ),
    FactField(
        "pregnancy.fetus_count",
        "enum",
        "本次妊娠胎数",
        ("单胎", "双胎", "多胎", "不确定"),
        candidate_eligible=True,
    ),
    FactField(
        "pregnancy.first_birth",
        "enum",
        "本次是否第一胎",
        ("是", "否", "还没确定"),
        candidate_eligible=True,
    ),
    FactField(
        "pregnancy.prior_birth_history",
        "string",
        "既往孕产情况",
        sensitivity="restricted_health",
        candidate_eligible=False,
    ),
    FactField(
        "pregnancy.birth_path",
        "enum",
        "当前计划或医生建议的分娩方式",
        ("顺产", "剖宫产", "还没确定"),
        candidate_eligible=True,
    ),
    FactField("pregnancy.city_or_country", "string", "当前所在城市或国家", max_length=255),
    FactField("pregnancy.birth_setting", "string", "建档、生产医院或生产地点", max_length=500),
    FactField(
        "pregnancy.medical_notes",
        "string",
        "基础疾病、长期用药、过敏或明确安全信息",
        sensitivity="restricted_health",
        candidate_eligible=False,
    ),
    FactField(
        "pregnancy.doctor_notes",
        "string_list",
        "医生明确提示的特殊情况",
        sensitivity="restricted_health",
        candidate_eligible=False,
    ),
    FactField(
        "pregnancy.feeding_intention",
        "enum",
        "宝宝出生后的喂养意向",
        ("母乳喂养", "混合喂养", "配方奶", "还没确定"),
        candidate_eligible=True,
    ),
    FactField("pregnancy.return_to_work_timing", "string", "产后预计返工时间", max_length=255),
    FactField("pregnancy.support_person", "string", "陪产或产后支持人及支持情况", max_length=1000),
    FactField("pregnancy.top_worries", "string_list", "当前最担心的待产或产后事项"),
    FactField("birth_plan.top_priorities", "string_list", "最希望医护知道的事项"),
    FactField("birth_plan.communication_preferences", "string_list", "与医护沟通的偏好"),
    FactField("birth_plan.priority_notes", "string", "希望额外告知医护的事项"),
    FactField("birth_plan.labor_preferences", "string_list", "生产过程照护偏好"),
    FactField("birth_plan.intervention_preferences", "string_list", "生产操作沟通偏好"),
    FactField("birth_plan.pain_relief_preferences", "string_list", "疼痛缓解或麻醉偏好"),
    FactField("birth_plan.pain_relief_notes", "string", "疼痛缓解或麻醉补充说明"),
    FactField("birth_plan.baby_after_birth_preferences", "string_list", "宝宝出生后的安排偏好"),
    FactField("birth_plan.if_plans_change", "string", "现场计划变化时的沟通偏好"),
    FactField(
        "birth_plan.emergency_authorization",
        "enum",
        "来不及充分沟通时的处理偏好",
        ("来不及细说时，优先按医生团队判断处理", "希望先联系我的伴侣/支持人", "希望尽量先直接告诉我", "还没确定"),
        candidate_eligible=True,
    ),
    FactField("birth_plan.hospital_questions_focus", "string_list", "希望提前向医院确认的问题"),
)
FACT_FIELD_BY_KEY = {field.key: field for field in FACT_FIELDS}
RESTRICTED_CANDIDATE_VALUE_MARKERS = (
    "确诊",
    "诊断",
    "糖尿病",
    "高血压",
    "甲亢",
    "甲减",
    "甲状腺",
    "哮喘",
    "癫痫",
    "抑郁症",
    "焦虑症",
    "感染",
    "过敏",
    "用药",
    "服药",
    "药物",
    "病史",
    "疾病",
    "乙肝",
    "梅毒",
    "艾滋",
    "贫血",
    "血糖",
    "血压异常",
    "自杀",
    "自残",
    "轻生",
    "伤害自己",
    "伤害宝宝",
    "diagnos",
    "diabetes",
    "hypertension",
    "thyroid",
    "asthma",
    "epilep",
    "depression",
    "anxiety disorder",
    "infection",
    "allerg",
    "medication",
    "medical history",
    "suicide",
    "self harm",
    "hurt myself",
    "hurt my baby",
)


@dataclass(frozen=True)
class FormFieldMapping:
    fact_key: str
    to_fact: Callable[[Any], Any] = lambda value: value
    to_form: Callable[[Any], Any] = lambda value: value


def _enum_aliases(aliases: dict[str, str]) -> Callable[[Any], Any]:
    return lambda value: aliases.get(str(value).strip(), str(value).strip())


def _reverse_enum_aliases(aliases: dict[str, str]) -> Callable[[Any], Any]:
    reversed_aliases = {canonical: form_value for form_value, canonical in aliases.items()}
    return lambda value: reversed_aliases.get(str(value).strip(), str(value).strip())


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    normalized = str(value or "").strip()
    return [normalized] if normalized else []


def _only_form_options(*options: str) -> Callable[[Any], Any]:
    allowed = frozenset(options)
    return lambda value: value if value in allowed else ""


_FETUS_HOSPITAL_ALIASES = {"三胎及以上": "多胎"}
_FETUS_PREGNANCY_ALIASES = {"不确定/暂不说": "不确定"}
_FIRST_BIRTH_PREGNANCY_ALIASES = {"不确定/暂不说": "还没确定"}
_BIRTH_PATH_HOSPITAL_ALIASES = {"还不确定": "还没确定"}
_FEEDING_HOSPITAL_ALIASES = {"亲喂母乳": "母乳喂养", "还不确定": "还没确定"}
_FEEDING_BIRTH_PLAN_ALIASES = {"母乳和配方奶都可能": "混合喂养", "还没想好": "还没确定"}


FORM_FIELD_MAPPINGS: dict[str, dict[str, FormFieldMapping]] = {
    PREGNANCY_PLAN_FORM_ID: {
        "current_week": FormFieldMapping("pregnancy.due_date_or_week"),
        "ivf": FormFieldMapping("pregnancy.ivf"),
        "fetus_count": FormFieldMapping(
            "pregnancy.fetus_count",
            to_fact=_enum_aliases(_FETUS_PREGNANCY_ALIASES),
            to_form=_reverse_enum_aliases(_FETUS_PREGNANCY_ALIASES),
        ),
        "age": FormFieldMapping("profile.age"),
        "first_birth": FormFieldMapping(
            "pregnancy.first_birth",
            to_fact=_enum_aliases(_FIRST_BIRTH_PREGNANCY_ALIASES),
            to_form=_reverse_enum_aliases(_FIRST_BIRTH_PREGNANCY_ALIASES),
        ),
        "prior_birth_history": FormFieldMapping("pregnancy.prior_birth_history"),
        "birth_path": FormFieldMapping("pregnancy.birth_path"),
        "city_or_country": FormFieldMapping("pregnancy.city_or_country"),
        "birth_hospital": FormFieldMapping("pregnancy.birth_setting"),
        "medical_notes": FormFieldMapping("pregnancy.medical_notes"),
        "doctor_notes": FormFieldMapping("pregnancy.doctor_notes", to_fact=_string_list, to_form=lambda value: "；".join(_string_list(value))),
    },
    HOSPITAL_BAG_FORM_ID: {
        "due_date_or_week": FormFieldMapping("pregnancy.due_date_or_week"),
        "first_birth": FormFieldMapping(
            "pregnancy.first_birth",
            to_form=_only_form_options("是", "否"),
        ),
        "fetus_count": FormFieldMapping(
            "pregnancy.fetus_count",
            to_fact=_enum_aliases(_FETUS_HOSPITAL_ALIASES),
            to_form=_reverse_enum_aliases(_FETUS_HOSPITAL_ALIASES),
        ),
        "pregnancy_history_or_notes": FormFieldMapping("pregnancy.doctor_notes", to_fact=_string_list, to_form=_string_list),
        "birth_path": FormFieldMapping(
            "pregnancy.birth_path",
            to_fact=_enum_aliases(_BIRTH_PATH_HOSPITAL_ALIASES),
            to_form=_reverse_enum_aliases(_BIRTH_PATH_HOSPITAL_ALIASES),
        ),
        "feeding_intention": FormFieldMapping(
            "pregnancy.feeding_intention",
            to_fact=_enum_aliases(_FEEDING_HOSPITAL_ALIASES),
            to_form=_reverse_enum_aliases(_FEEDING_HOSPITAL_ALIASES),
        ),
        "return_to_work_timing": FormFieldMapping("pregnancy.return_to_work_timing"),
        "support_person": FormFieldMapping(
            "pregnancy.support_person",
            to_form=_only_form_options("有人全天帮忙", "白天主要自己", "夜间主要自己", "支持少", "不确定"),
        ),
        "top_worries": FormFieldMapping("pregnancy.top_worries", to_fact=_string_list, to_form=_string_list),
    },
    BIRTH_PLAN_FORM_ID: {
        "due_date_or_week": FormFieldMapping("pregnancy.due_date_or_week"),
        "birth_path": FormFieldMapping("pregnancy.birth_path"),
        "birth_setting": FormFieldMapping("pregnancy.birth_setting"),
        "first_birth": FormFieldMapping("pregnancy.first_birth"),
        "top_priorities": FormFieldMapping("birth_plan.top_priorities", to_fact=_string_list, to_form=_string_list),
        "support_person": FormFieldMapping("pregnancy.support_person"),
        "communication_preferences": FormFieldMapping("birth_plan.communication_preferences", to_fact=_string_list, to_form=_string_list),
        "priority_notes": FormFieldMapping("birth_plan.priority_notes"),
        "labor_preferences": FormFieldMapping("birth_plan.labor_preferences", to_fact=_string_list, to_form=_string_list),
        "intervention_preferences": FormFieldMapping("birth_plan.intervention_preferences", to_fact=_string_list, to_form=_string_list),
        "pain_relief_preferences": FormFieldMapping("birth_plan.pain_relief_preferences", to_fact=_string_list, to_form=_string_list),
        "pain_relief_notes": FormFieldMapping("birth_plan.pain_relief_notes"),
        "feeding_intention": FormFieldMapping(
            "pregnancy.feeding_intention",
            to_fact=_enum_aliases(_FEEDING_BIRTH_PLAN_ALIASES),
            to_form=_reverse_enum_aliases(_FEEDING_BIRTH_PLAN_ALIASES),
        ),
        "baby_after_birth_preferences": FormFieldMapping("birth_plan.baby_after_birth_preferences", to_fact=_string_list, to_form=_string_list),
        "if_plans_change": FormFieldMapping("birth_plan.if_plans_change"),
        "emergency_authorization": FormFieldMapping("birth_plan.emergency_authorization"),
        "hospital_questions_focus": FormFieldMapping("birth_plan.hospital_questions_focus", to_fact=_string_list, to_form=_string_list),
        "medical_notes": FormFieldMapping("pregnancy.medical_notes"),
    },
}


def normalize_fact_value(fact_key: str, value: Any) -> Any:
    field = FACT_FIELD_BY_KEY.get(fact_key)
    if field is None:
        raise ValueError("unknown fact key")
    if field.value_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int) or not 12 <= value <= 70:
            raise ValueError("invalid integer fact")
        return value
    if field.value_type == "string_list":
        items = _string_list(value)
        if not items or len(items) > 12 or any(len(item) > 500 for item in items):
            raise ValueError("invalid list fact")
        return items
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > field.max_length:
        raise ValueError("invalid string fact")
    if field.value_type == "enum" and normalized not in field.enum_values:
        raise ValueError("invalid enum fact")
    return normalized


def form_values_to_fact_inputs(*, form_id: str, values: dict[str, Any]) -> list[FactInput]:
    mappings = FORM_FIELD_MAPPINGS.get(form_id, {})
    inputs: list[FactInput] = []
    for field_id, mapping in mappings.items():
        if field_id not in values or values[field_id] in (None, "", []):
            continue
        try:
            normalized = normalize_fact_value(mapping.fact_key, mapping.to_fact(values[field_id]))
        except (TypeError, ValueError):
            continue
        inputs.append(FactInput(mapping.fact_key, normalized, "explicit", f"form:{field_id}"))
    return sorted(inputs, key=lambda item: item.fact_key)


def facts_to_form_defaults(*, form_id: str, facts: dict[str, Any]) -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    for field_id, mapping in FORM_FIELD_MAPPINGS.get(form_id, {}).items():
        if mapping.fact_key not in facts:
            continue
        try:
            value = mapping.to_form(normalize_fact_value(mapping.fact_key, facts[mapping.fact_key]))
            if value in (None, "", []):
                continue
            defaults[field_id] = value
        except (TypeError, ValueError):
            continue
    return defaults


def extractor_catalog_payload() -> list[dict[str, Any]]:
    return [
        {
            "fact_key": field.key,
            "value_type": field.value_type,
            "description": field.description,
            "enum_values": list(field.enum_values),
            "expected_subject": expected_conversation_subject(field.key),
        }
        for field in FACT_FIELDS
        if field.candidate_eligible
    ]


def expected_conversation_subject(fact_key: str) -> str:
    return "current_pregnancy" if fact_key.startswith("pregnancy.") else "self"


def conversation_candidate_ttl_days(fact_key: str) -> int:
    field = FACT_FIELD_BY_KEY[fact_key]
    return max(1, field.candidate_ttl_days)


def candidate_value_contains_restricted_content(value: Any) -> bool:
    if isinstance(value, list):
        text = " ".join(str(item) for item in value)
    else:
        text = str(value or "")
    normalized = "".join(text.lower().split())
    return any("".join(marker.lower().split()) in normalized for marker in RESTRICTED_CANDIDATE_VALUE_MARKERS)


def candidate_value_matches_safe_shape(*, fact_key: str, value: Any) -> bool:
    if fact_key == "pregnancy.due_date_or_week":
        normalized = "".join(str(value or "").split())
        return bool(
            re.fullmatch(r"(?:孕)?\d{1,2}(?:\+\d)?周", normalized)
            or re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized)
            or re.fullmatch(r"\d{4}年\d{1,2}月\d{1,2}日", normalized)
        )
    return fact_key in {
        "profile.age",
        "pregnancy.fetus_count",
        "pregnancy.first_birth",
        "pregnancy.birth_path",
        "pregnancy.feeding_intention",
        "birth_plan.emergency_authorization",
    }
