from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from ..types import RuntimeInputs

HOSPITAL_BAG_CART_URL = "/hospital-bag-cart"
HOSPITAL_BAG_CART_LINK = f"[打开待产包一键打包下单页]({HOSPITAL_BAG_CART_URL})"
PUMP_ITEM = {
    "label": "便携式吸奶器",
    "quantity": "1台",
    "priority": "recommended",
    "note": "如果计划母乳或混合喂养，可作为初期涨奶或追奶的备用选择；具体使用以医院和哺乳顾问建议为准。",
}
HOSPITAL_BAG_CART_ASSISTANT_FOLLOWUP = {
    "kind": "hospital_bag_cart",
    "message": (
        "你的待产包已经生成好了哦～我也顺手把清单里适合直接购买的妈妈/宝宝母婴用品整理成了购物车，"
        "方便你打开后慢慢核对、删减；证件、医院确认项和医疗相关内容不会放进去。\n\n"
        f"**{HOSPITAL_BAG_CART_LINK}**\n\n"
        "不用急着一次买完，先按医院会提供和家里已有的情况删一删就好。"
    ),
}
BIRTH_PLAN_DISCLAIMER = (
    "这张卡只用于沟通。请优先遵循医生和医院建议，尤其是因安全原因需要调整计划时。"
)
BIRTH_PLAN_ASSISTANT_FOLLOWUP = {
    "kind": "birth_plan_card_guidance",
    "message": "你可以提前和医院确认，并在产检或入院前把这张卡给医生/护士看，用它快速沟通你的重点偏好和需要讨论的问题。",
}
BIRTH_PLAN_CARD_SECTION_ITEM_LIMIT = 20
REMOVED_HOSPITAL_BAG_FORM_FIELD_IDS = {
    "hospital_rules_or_notes",
    "existing_checklist_or_photo_note",
}
EXCLUSIVE_BIRTH_PLAN_MULTI_SELECT_OPTIONS = {
    "我还没想好，请帮我整理成温和版本",
    "不需要持续解释，必要时再说就好",
    "无特别偏好，听医生安排",
    "听医生判断即可",
    "灌肠/剃毛：希望按医院常规即可",
    "暂未决定，听医生建议",
    "未确定",
    "还没确定",
    "还没想好",
}
PLACEHOLDER_VALUES = {"", "to confirm", "待确认", "未确定", "不确定", "还没确定", "还没想好", "none", "n/a"}
BIRTH_PATH_ALIASES = {
    "vaginal": "顺产",
    "natural": "顺产",
    "顺产": "顺产",
    "planned_c_section": "剖宫产",
    "c_section": "剖宫产",
    "c-section": "剖宫产",
    "cesarean": "剖宫产",
    "剖宫产": "剖宫产",
    "计划剖宫产": "剖宫产",
    "剖腹产": "剖宫产",
    "刨腹产": "剖宫产",
}
HOSPITAL_BAG_FORM_FIELDS = [
    {
        "id": "due_date_or_week",
        "label": "基本信息｜预产期或当前孕周",
        "type": "text",
        "required": True,
        "placeholder": "例如：2026-06-12 或 37 周",
    },
    {
        "id": "first_birth",
        "label": "基本信息｜是否第一胎",
        "type": "select",
        "required": True,
        "options": ["是", "否", "不确定"],
    },
    {
        "id": "age",
        "label": "基本信息｜妈妈年龄",
        "type": "text",
        "required": False,
        "placeholder": "例如：32；不想填可以留空",
    },
    {
        "id": "bmi_or_weight_context",
        "label": "基本信息｜BMI 或身高体重情况",
        "type": "text",
        "required": False,
        "placeholder": "例如：BMI 24，或身高体重；不确定可留空",
    },
    {
        "id": "pregnancy_history_or_notes",
        "label": "基本信息｜妊娠病史或特殊注意事项",
        "type": "textarea",
        "required": False,
        "placeholder": "例如：妊娠糖尿病、过敏史、医生提醒、行动不便等；没有可留空。",
    },
    {
        "id": "birth_path",
        "label": "生产信息｜计划分娩方式",
        "type": "select",
        "required": True,
        "options": ["顺产", "剖宫产", "未确定"],
    },
    {
        "id": "expected_stay",
        "label": "生产信息｜预计住院时长",
        "type": "select",
        "required": False,
        "options": ["不确定", "1 天", "2-3 天", "4 天或以上", "医生/医院建议为准"],
    },
    {
        "id": "support_person",
        "label": "生产信息｜陪产人或支持人情况",
        "type": "select",
        "required": False,
        "options": ["有，且需要准备物品", "有，但不需要准备物品", "暂时没有", "不确定"],
    },
    {
        "id": "birth_setting",
        "label": "医院信息｜医院、地区或生产地点",
        "type": "text",
        "required": False,
        "placeholder": "例如：某某医院、公立医院、私立医院、月子中心配套医院，或暂未确定",
    },
    {
        "id": "hospital_provided_items",
        "label": "医院信息｜已知医院会提供的物品",
        "type": "textarea",
        "required": False,
        "placeholder": "例如：产褥垫、纸尿裤、宝宝衣物、奶瓶、毛巾。不确定可留空。",
    },
    {
        "id": "feeding_intention",
        "label": "偏好信息｜喂养意向",
        "type": "select",
        "required": True,
        "options": ["母乳", "配方", "混合", "未确定"],
    },
]
BIRTH_PLAN_FORM_FIELDS = [
    {
        "id": "due_date_or_week",
        "label": "基本信息｜现在怀孕多久/预产期",
        "type": "text",
        "required": True,
        "placeholder": "例如：2026-06-12 或 37 周",
    },
    {
        "id": "birth_path",
        "label": "基本信息｜医生目前建议的生产方式",
        "type": "select",
        "required": True,
        "options": ["顺产", "剖宫产", "还没确定"],
    },
    {
        "id": "birth_setting",
        "label": "基本信息｜准备在哪家医院/哪里生",
        "type": "text",
        "required": False,
        "placeholder": "例如：某某医院、助产中心，或暂未确定",
    },
    {
        "id": "first_birth",
        "label": "基本信息｜是不是第一胎",
        "type": "select",
        "required": False,
        "options": ["是", "否", "还没确定"],
    },
    {
        "id": "top_priorities",
        "label": "支持与沟通｜最希望医护知道的事",
        "type": "multi_select",
        "required": True,
        "options": [
            "宝宝出生后，想尽早抱一抱/贴一贴",
            "想尽早试着喂母乳",
            "希望伴侣/支持人尽量陪在身边",
            "希望医护多鼓励我、告诉我进展",
            "一些非必要操作，希望先和我沟通",
        ],
    },
    {
        "id": "support_person",
        "label": "支持与沟通｜谁陪你、希望 TA 帮什么",
        "type": "text",
        "required": False,
        "placeholder": "例如：伴侣陪产并参与重要决定；妈妈在产后帮忙照顾",
    },
    {
        "id": "communication_preferences",
        "label": "支持与沟通｜希望医护怎么和你沟通",
        "type": "multi_select",
        "required": False,
        "options": [
            "做操作前，先告诉我为什么需要",
            "做重要决定前，先问问我的想法",
            "重要决定也请同步伴侣/支持人",
            "请用简单清楚的话说明",
            "计划有变化时，请先说原因和选择",
            "需要翻译或语言支持",
        ],
    },
    {
        "id": "priority_notes",
        "label": "支持与沟通｜还有什么想补充告诉医护",
        "type": "textarea",
        "required": False,
        "placeholder": "如果上面的选项没覆盖，可以简单写一句；不确定可留空。",
    },
    {
        "id": "labor_preferences",
        "label": "生产过程｜生宝宝时希望怎么被照顾",
        "type": "multi_select",
        "required": False,
        "options": [
            "医生允许时，希望可以走动或换姿势",
            "宝宝心跳监护怎么做，希望先说明一下",
            "希望可以用分娩球、热敷或按摩让自己舒服一点",
            "想提前确认生产时能不能喝水或吃点东西",
            "希望环境安静一点、灯光柔和一点",
        ],
    },
    {
        "id": "intervention_preferences",
        "label": "生产过程｜需要先说清楚的操作",
        "type": "multi_select",
        "required": False,
        "options": [
            "如果需要侧切，请先说明原因再和我沟通",
            "如果需要产钳或吸引，请先解释为什么需要",
            "如果需要人工破水，请先和我说明",
            "灌肠或剃毛前，希望先告诉我是否必须",
        ],
    },
    {
        "id": "pain_relief_preferences",
        "label": "疼痛和舒适｜生产时怎么帮你舒服一点",
        "type": "multi_select",
        "required": False,
        "options": [
            "想提前了解有哪些减痛/麻醉选择",
            "如果安全允许，先试试呼吸、姿势、按摩来缓解",
            "我倾向使用无痛/硬膜外，想提前沟通安排",
            "有点担心副作用或恢复，想先了解清楚再决定",
            "如果剖宫产，希望手术麻醉前充分说明",
        ],
    },
    {
        "id": "pain_relief_notes",
        "label": "疼痛和舒适｜其他关于疼痛缓解/麻醉的想法",
        "type": "textarea",
        "required": False,
        "placeholder": "如果上面的选项没覆盖，可以简单写一句；不确定可留空。",
    },
    {
        "id": "feeding_intention",
        "label": "宝宝出生后｜准备怎么喂宝宝",
        "type": "select",
        "required": False,
        "options": ["母乳喂养", "母乳和配方奶都可能", "配方奶", "还没想好"],
    },
    {
        "id": "baby_after_birth_preferences",
        "label": "宝宝出生后｜宝宝出生后希望怎么安排",
        "type": "multi_select",
        "required": False,
        "options": [
            "宝宝出生后，想尽早抱一抱/贴一贴",
            "想尽早试着亲喂/喂母乳",
            "如果医院允许，希望晚一点剪脐带",
            "希望宝宝尽量和我在一起",
            "给宝宝做检查或护理前，希望先告诉我",
            "打针、疫苗或新生儿检查前，希望先说明",
            "如果医院允许，希望伴侣/家人剪脐带",
            "如果医院允许，第一次洗澡晚一点",
            "如果宝宝需要离开我身边，请说明原因和大概多久",
            "给宝宝用配方奶或奶瓶前，请先和我沟通",
        ],
    },
    {
        "id": "if_plans_change",
        "label": "临时变化｜如果现场安排变了，希望怎么沟通",
        "type": "textarea",
        "required": False,
        "placeholder": "例如：请尽量解释原因；请让伴侣参与决定；请用简单语言说明选择。",
    },
    {
        "id": "emergency_authorization",
        "label": "临时变化｜如果来不及慢慢沟通，希望怎么处理",
        "type": "select",
        "required": False,
        "options": ["来不及细说时，优先按医生团队判断处理", "希望先联系我的伴侣/支持人", "希望尽量先直接告诉我", "还没确定"],
    },
    {
        "id": "hospital_questions_focus",
        "label": "提前问医院｜想提前问医院的问题",
        "type": "multi_select",
        "required": False,
        "options": [
            "陪产和探视怎么安排",
            "能不能拍照或录像",
            "生产时能不能喝水或吃点东西",
            "无痛或麻醉什么时候可以沟通",
            "宝宝出生后的护理流程",
            "产后有没有母乳喂养支持",
            "大概住几天、怎么出院",
            "紧急情况会怎么沟通和决定",
        ],
    },
    {
        "id": "medical_notes",
        "label": "提前问医院｜过敏、医生提醒或其他安全信息",
        "type": "textarea",
        "required": False,
        "placeholder": "只填写你明确知道的信息，例如：过敏、医生已说明的限制、医院要求。不确定可留空。",
    },
]
HOSPITAL_BAG_MISSING_LABELS = {
    "due_date_or_week": "预产期或当前孕周",
    "first_birth": "是否第一胎",
    "birth_path": "计划分娩方式",
    "feeding_intention": "喂养意向",
}
HOSPITAL_BAG_DISCLAIMER = "请优先遵循医院要求和医生/助产士的具体指导。"
HOSPITAL_BAG_PROVIDED_ITEM_ALIASES = {
    "尿布": ("纸尿裤",),
    "宝宝衣服": ("宝宝出院衣物",),
    "宝宝衣物": ("宝宝出院衣物",),
    "新生儿衣服": ("宝宝出院衣物",),
    "新生儿衣物": ("宝宝出院衣物",),
    "卫生巾": ("产褥垫/产妇卫生巾",),
    "产妇卫生巾": ("产褥垫/产妇卫生巾",),
    "产褥垫": ("产褥垫/产妇卫生巾", "备用产褥垫/卫生巾"),
    "湿巾": ("湿巾/棉柔巾", "纸巾/湿巾"),
    "棉柔巾": ("湿巾/棉柔巾",),
}


def create_form(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    form_id = str(args.get("form_id", "form"))
    fields = _normalize_form_fields(args.get("fields", []))
    description = str(args.get("description", ""))
    if form_id == "hospital_bag_intake":
        fields = [field for field in fields if field.get("id") not in REMOVED_HOSPITAL_BAG_FORM_FIELD_IDS]
        fields = [_without_field_help_text(field) for field in fields]
        description = ""
    elif form_id == "birth_plan_card_intake":
        fields = [_sanitize_birth_plan_form_field(_without_field_help_text(field)) for field in fields]
        description = ""
    return {
        "tool_name": "ui_form_create",
        "status": "form_created",
        "form": {
            "id": form_id,
            "title": args.get("title", ""),
            "description": description,
            "submit_label": args.get("submit_label", "确认"),
            "fields": fields,
        },
    }


def create_hospital_bag_form(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    default_values = _dict_value(args.get("default_values"))
    fields: list[dict[str, Any]] = []
    for template in HOSPITAL_BAG_FORM_FIELDS:
        field = dict(template)
        value = _first_text(default_values.get(field["id"]))
        if value and _normalized_placeholder(value) not in PLACEHOLDER_VALUES:
            field["default_value"] = _normalize_hospital_bag_form_value(field["id"], value)
        fields.append(field)
    return {
        "tool_name": "ui_form_create",
        "status": "form_created",
        "form": {
            "id": "hospital_bag_intake",
            "title": "信息采集",
            "description": "",
            "submit_label": "开始生成",
            "fields": fields,
        },
    }


def create_hospital_bag_card(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    form_data = _confirmed_form_data(inputs) or _dict_value(args.get("confirmed_form_data"))
    generation_mode = str(args.get("generation_mode") or "standard")
    card_json = _build_hospital_bag_card_json(form_data, generation_mode, inputs)
    _normalize_hospital_bag_scene_groups(card_json["packing_groups"])
    return {
        "tool_name": "ui_card_create",
        "status": "card_created",
        "card": {
            "card_type": "hospital_bag_card",
            "schema_version": "1.0",
            "card_json": card_json,
        },
        "assistant_followup": dict(HOSPITAL_BAG_CART_ASSISTANT_FOLLOWUP),
    }


def create_birth_plan_form(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    default_values = _dict_value(args.get("default_values"))
    fields: list[dict[str, Any]] = []
    for template in BIRTH_PLAN_FORM_FIELDS:
        field = _sanitize_birth_plan_form_field(dict(template))
        value = default_values.get(field["id"])
        if field["id"] == "support_person":
            value = _first_text(value, default_values.get("support_people"))
        normalized_value = _normalize_birth_plan_form_value(field["id"], value)
        if _has_meaningful_value(normalized_value):
            field["default_value"] = normalized_value
        fields.append(field)
    return {
        "tool_name": "ui_form_create",
        "status": "form_created",
        "form": {
            "id": "birth_plan_card_intake",
            "title": "信息采集",
            "description": "",
            "submit_label": "生成我的沟通卡",
            "fields": fields,
        },
    }


def create_birth_plan_card(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    form_data = _confirmed_form_data(inputs) or _dict_value(args.get("confirmed_form_data"))
    card_json: dict[str, Any] = {
        "card_type": "birth_plan_card",
        "schema_version": "1.0",
        "title": "分娩沟通卡",
    }
    assistant_followup = _prepare_birth_plan_card(card_json, inputs, form_data)
    return {
        "tool_name": "ui_card_create",
        "status": "card_created",
        "card": {
            "card_type": "birth_plan_card",
            "schema_version": "1.0",
            "card_json": card_json,
        },
        "assistant_followup": assistant_followup,
    }


def _build_hospital_bag_card_json(form_data: dict[str, Any], generation_mode: str, inputs: RuntimeInputs) -> dict[str, Any]:
    due_date_or_week = _first_text(form_data.get("due_date_or_week")) or "待确认"
    birth_path = _normalize_birth_path(_first_text(form_data.get("birth_path"))) or "待确认"
    feeding_intention = _normalize_feeding_intention(_first_text(form_data.get("feeding_intention"))) or "待确认"
    first_birth = _normalize_first_birth(_first_text(form_data.get("first_birth"))) or "待确认"
    birth_setting = _first_text(form_data.get("birth_setting")) or "待确认"
    expected_stay = _first_text(form_data.get("expected_stay")) or "待确认"
    support_person = _first_text(form_data.get("support_person")) or "待确认"
    provided_items = _split_hospital_provided_items(form_data.get("hospital_provided_items"))
    stage = _hospital_bag_stage(due_date_or_week, generation_mode, inputs)
    missing_fields = [
        label
        for field_id, label in HOSPITAL_BAG_MISSING_LABELS.items()
        if _normalized_placeholder(_first_text(form_data.get(field_id))) in PLACEHOLDER_VALUES
    ]
    context: dict[str, Any] = {
        "stage": stage,
        "birth_path": birth_path,
        "feeding_intention": feeding_intention,
        "first_birth": first_birth,
        "expected_stay": expected_stay,
        "support_person": support_person,
        "provided_items": provided_items,
    }
    hospital_questions = _hospital_bag_confirmation_questions(context)
    packing_groups = _hospital_bag_packing_groups(context)
    personalized_notes = _hospital_bag_personalized_notes(context, form_data)
    timeline = _hospital_bag_timeline(stage)
    focus_items = _hospital_bag_focus_items(stage, context)
    return {
        "card_type": "hospital_bag_card",
        "schema_version": "1.0",
        "title": "待产包",
        "subtitle": "个性化入院物品清单",
        "owner": {
            "due_date_or_week": due_date_or_week,
            "birth_setting": birth_setting,
            "birth_path": birth_path,
            "first_birth": first_birth,
            "feeding_intention": feeding_intention,
            "support_person": support_person,
        },
        "hospital_context": {
            "expected_stay": expected_stay,
            "hospital_provided_items": provided_items,
            "items_to_confirm_with_hospital": hospital_questions[:5],
        },
        "focus_items": focus_items,
        "hospital_questions": hospital_questions[:8],
        "packing_groups": packing_groups,
        "missing_or_to_buy": [],
        "timeline": timeline,
        "personalized_notes": personalized_notes,
        "missing_fields": missing_fields,
        "disclaimer": HOSPITAL_BAG_DISCLAIMER,
    }


def _hospital_bag_packing_groups(context: dict[str, Any]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = [
        {"group_id": "documents", "title": "证件文件包", "items": _hospital_bag_document_items(context)},
        {"group_id": "mom_hospital_bag", "title": "妈妈住院包", "items": _hospital_bag_mom_items(context)},
        {"group_id": "baby_discharge_bag", "title": "宝宝出院包", "items": _hospital_bag_baby_items(context)},
        {"group_id": "support_person_bag", "title": "陪产人包", "items": _hospital_bag_support_items(context)},
        {"group_id": "car_backup_bag", "title": "车上备用包", "items": _hospital_bag_car_items(context)},
    ]
    postpartum_items = _hospital_bag_postpartum_items(context)
    if postpartum_items:
        groups.append({"group_id": "postpartum_home_first_week", "title": "产后回家第一周用品", "items": postpartum_items})
    return [
        {**group, "items": _filter_provided_items(group["items"], context.get("provided_items", []))}
        for group in groups
        if group.get("items")
    ]


def _hospital_bag_document_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"label": "身份证件", "priority": "must", "copy_requirement": "原件", "note": "妈妈和陪产人证件分开放，入院登记时更好找。"},
        {"label": "医保卡/保险卡", "priority": "must", "copy_requirement": "原件"},
        {"label": "产检本/产检资料", "priority": "must", "copy_requirement": "原件"},
        {"label": "检查报告/化验单", "priority": "recommended", "copy_requirement": "按医院要求"},
        {"label": "医院预登记信息", "priority": "confirm_first", "confirm_question": "确认是否已完成医院预登记，以及入院当天需要出示什么。"},
        {"label": "银行卡/手机支付", "priority": "must"},
        {"label": "紧急联系人信息", "priority": "recommended"},
        {"label": "医生/医院联系电话", "priority": "recommended"},
        {"label": "分娩沟通卡", "priority": "nice_to_have", "note": "如果已经做过，可以和产检资料放在一起。"},
        {
            "label": "准生证/户口本",
            "priority": "confirm_first",
            "confirm_question": "确认医院是否要求携带准生证、户口本及复印件。",
        },
    ]


def _hospital_bag_mom_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    items = [
        {"label": "手机充电线和充电器", "priority": "must", "note": "长充电线更适合病床旁使用。"},
        {"label": "宽松出院衣物", "priority": "must", "quantity": "1套"},
        {"label": "开襟睡衣/哺乳睡衣", "priority": "recommended", "quantity": "1-2套"},
        {"label": "哺乳文胸/舒适内衣", "priority": "recommended", "quantity": "2-3件"},
        {"label": "防滑拖鞋", "priority": "must", "quantity": "1双"},
        {"label": "吸管杯", "priority": "must", "quantity": "1个"},
        {"label": "产褥垫/产妇卫生巾", "priority": "must", "quantity": _postpartum_pad_quantity(context)},
        {"label": "一次性内裤", "priority": "recommended", "quantity": "若干条"},
        {"label": "洗漱用品", "priority": "recommended", "quantity": "旅行装"},
        {"label": "纸巾/湿巾", "priority": "recommended", "quantity": "少量"},
        {"label": "毛巾", "priority": "recommended", "quantity": "1-2条"},
        {"label": "束发用品", "priority": "nice_to_have"},
        {"label": "润唇膏", "priority": "nice_to_have"},
        {"label": "外套/披肩", "priority": "recommended", "quantity": "1件"},
        {
            "label": "胎监带",
            "priority": "confirm_first",
            "confirm_question": "确认医院是否要求自带胎监带，以及需要几条。",
        },
    ]
    if context.get("birth_path") == "剖宫产":
        items.insert(2, {"label": "高腰宽松内裤", "priority": "recommended", "quantity": "若干条", "note": "更不容易压到腹部。"})
        items.insert(3, {"label": "不压腹出院裤/裙", "priority": "recommended", "quantity": "1套"})
        items.append(
            {
                "label": "收腹带",
                "priority": "confirm_first",
                "confirm_question": "剖宫产先确认医生或医院是否建议使用收腹带。",
            }
        )
    return items


def _hospital_bag_baby_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"label": "宝宝出院衣物", "priority": "must", "quantity": "1套"},
        {"label": "备用连体衣", "priority": "recommended", "quantity": "1-2套"},
        {"label": "包被", "priority": "must", "quantity": "1条"},
        {"label": "小毯子", "priority": "nice_to_have", "quantity": "1条"},
        {"label": "纸尿裤", "priority": "confirm_first", "confirm_question": "确认医院是否提供纸尿裤；如果不提供，再问建议数量。"},
        {"label": "湿巾/棉柔巾", "priority": "recommended", "quantity": "1-2包"},
        {"label": "帽子/袜子", "priority": "recommended", "quantity": "各1-2件"},
        {"label": "口水巾/小方巾", "priority": "nice_to_have", "quantity": "2-3条"},
        {"label": "奶瓶", "priority": "confirm_first", "confirm_question": "确认医院是否允许或需要自带奶瓶。"},
        {"label": "安全提篮/安全座椅", "priority": "confirm_first", "confirm_question": "确认出院交通是否需要安全提篮或安全座椅。"},
    ]


def _hospital_bag_support_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    if not _support_person_needs_bag(context):
        return [
            {"label": "远程联系人名单", "priority": "must", "note": "写清楚临产、入院和出院时分别联系谁。"},
            {"label": "去医院交通方案", "priority": "must"},
            {"label": "出院接送安排", "priority": "recommended"},
            {"label": "家中照护安排", "priority": "recommended", "note": "如有大宝、宠物或家务支持，提前定好负责人。"},
            {"label": "紧急备用联系人", "priority": "recommended"},
        ]
    return [
        {"label": "陪产人身份证件", "priority": "must", "copy_requirement": "原件"},
        {"label": "手机充电器", "priority": "must"},
        {"label": "充电宝", "priority": "recommended"},
        {"label": "换洗衣物", "priority": "recommended", "quantity": "1套"},
        {"label": "外套", "priority": "recommended", "quantity": "1件"},
        {"label": "洗漱用品", "priority": "recommended", "quantity": "1套"},
        {"label": "水和零食", "priority": "recommended", "quantity": "按住院天数"},
        {"label": "停车/支付用品", "priority": "recommended"},
        {"label": "记录工具", "priority": "nice_to_have", "note": "用于记医生交代、出生信息和喂养时间。"},
        {"label": "妈妈的沟通偏好", "priority": "nice_to_have", "note": "提前知道哪些事要先问妈妈。"},
    ]


def _hospital_bag_car_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"label": "备用产褥垫/卫生巾", "priority": "recommended", "quantity": "少量"},
        {"label": "纸巾/湿巾", "priority": "recommended", "quantity": "少量"},
        {"label": "水", "priority": "recommended", "quantity": "少量"},
        {"label": "备用衣物", "priority": "nice_to_have", "quantity": "1套"},
        {"label": "医院路线和停车信息", "priority": "recommended"},
        {"label": "塑料袋/收纳袋", "priority": "recommended"},
        {"label": "备用毛巾", "priority": "nice_to_have", "quantity": "1条"},
        {"label": "车内充电线", "priority": "recommended"},
        {"label": "夜间入口信息", "priority": "confirm_first", "confirm_question": "确认夜间急诊或产科入口在哪里。"},
    ]


def _hospital_bag_postpartum_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    feeding = context.get("feeding_intention")
    if feeding == "配方":
        return [
            {"label": "奶瓶", "priority": "confirm_first", "confirm_question": "确认医院是否允许自带奶瓶，或是否由医院提供。"},
            {"label": "配方奶", "priority": "confirm_first", "confirm_question": "确认医院是否允许自带配方奶，以及品牌或规格要求。"},
            {"label": "奶瓶清洁用品", "priority": "recommended", "quantity": "少量"},
            {"label": "奶嘴", "priority": "recommended", "quantity": "少量"},
            {"label": "消毒设备", "priority": "nice_to_have", "note": "按家庭习惯准备，不一定需要提前买大件。"},
            {"label": "喂养记录工具", "priority": "recommended"},
        ]
    items = [
        {"label": "哺乳文胸/哺乳背心", "priority": "recommended", "quantity": "2-3件"},
        {"label": "防溢乳垫", "priority": "recommended", "quantity": "5-10片"},
        {"label": "便携式吸奶器", "priority": "recommended", "quantity": "1台", "note": "母乳或混合喂养时可作为备用，不是必须购买。"},
        {"label": "储奶袋/储奶瓶", "priority": "recommended", "quantity": "少量"},
        {"label": "乳头霜", "priority": "recommended", "quantity": "1支"},
        {"label": "乳盾", "priority": "confirm_first", "confirm_question": "是否需要乳盾，建议先听医院或哺乳顾问建议。"},
        {"label": "哺乳枕", "priority": "nice_to_have"},
        {"label": "小夜灯", "priority": "nice_to_have"},
        {"label": "宝宝尿布台用品", "priority": "recommended"},
        {"label": "喂养记录工具", "priority": "recommended"},
    ]
    return items if feeding in {"母乳", "混合", "待确认", "未确定"} else []


def _hospital_bag_confirmation_questions(context: dict[str, Any]) -> list[str]:
    questions = [
        "准生证/户口本：确认医院是否要求携带原件和复印件。",
        "纸尿裤/宝宝衣物：确认医院是否提供，避免重复携带。",
        "胎监带：确认医院是否要求自带，以及需要几条。",
        "陪产/探视：确认是否允许陪产或探视，以及陪产人是否可以过夜。",
        "水和零食：确认产房和病区是否允许携带。",
    ]
    if context.get("birth_path") == "剖宫产":
        questions.append("收腹带/术后用品：剖宫产先问医生或医院是否建议准备。")
    if context.get("feeding_intention") == "配方":
        questions.append("奶瓶/配方奶：确认医院是否允许携带，或是否由医院提供。")
    elif context.get("feeding_intention") in {"母乳", "混合"}:
        questions.append("母乳喂养支持：确认医院是否有产后哺乳指导或泌乳顾问资源。")
    if context.get("stage") in {"packing", "immediate"}:
        questions.append("住院时长/出院要求：确认预计住院几天，以及宝宝出院衣物是否有要求。")
    return _dedupe_strings(questions)


def _hospital_bag_focus_items(stage: str, context: dict[str, Any]) -> list[str]:
    base = ["身份证件", "医保卡/保险卡", "产检资料", "手机充电线和充电器", "宝宝出院衣物和包被"]
    if stage in {"packing", "immediate"}:
        base.insert(3, "产褥垫/产妇卫生巾")
        base.insert(4, "一次性内裤")
    if context.get("feeding_intention") in {"母乳", "混合"}:
        base.append("哺乳文胸/防溢乳垫")
    return base[:7]


def _hospital_bag_personalized_notes(context: dict[str, Any], form_data: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    stage_labels = {
        "planning": "现在更适合先把医院要求和大方向定下来，不用急着一次买齐。",
        "purchase": "这个阶段可以开始集中准备基础物品，但医院会提供的东西先别重复买。",
        "packing": "已经接近实际打包阶段，清单优先保留能直接装包的物品。",
        "immediate": "现在最重要的是随手能拿走，先保留证件、妈妈护理和宝宝出院基础物品。",
    }
    notes.append(stage_labels.get(context.get("stage"), stage_labels["purchase"]))
    if context.get("birth_path") == "剖宫产":
        notes.append("你选择了剖宫产，所以保留了更宽松、方便拿取和术后更友好的物品提醒。")
    if context.get("feeding_intention") in {"母乳", "混合"}:
        notes.append("你有母乳喂养意向，所以保留哺乳文胸、防溢乳垫和吸奶器备用项。")
    elif context.get("feeding_intention") == "配方":
        notes.append("你选择配方喂养，所以奶瓶和配方奶先放到医院确认项里，不默认当作必带。")
    if _first_text(form_data.get("pregnancy_history_or_notes")):
        notes.append("你填写的特殊注意事项只用于打包和医院确认提醒，不做医学判断。")
    return notes[:3]


def _hospital_bag_timeline(stage: str) -> list[str]:
    if stage == "planning":
        return ["下次产检前：先问医院入院材料和提供物品。", "32 周前后：再把基础母婴用品补齐。"]
    if stage == "purchase":
        return ["这两周：先买齐妈妈护理、宝宝出院和证件收纳用品。", "35-36 周：把主包和证件袋实际装好。"]
    if stage == "packing":
        return ["今天或本周：把证件袋、妈妈包和宝宝出院包分开装好。", "出发前：只复核手机、充电线、证件和医院联系信息。"]
    return ["现在：证件、手机、妈妈护理和宝宝出院物品先放固定位置。", "出发前：联系医院或医生，确认入院入口和需要携带的材料。"]


def _hospital_bag_stage(due_date_or_week: str, generation_mode: str, inputs: RuntimeInputs) -> str:
    if generation_mode == "immediate":
        return "immediate"
    week = _pregnancy_week(due_date_or_week, inputs)
    if week is None:
        return "purchase"
    if week >= 37:
        return "immediate"
    if week >= 36:
        return "packing"
    if week >= 32:
        return "purchase"
    return "planning"


def _pregnancy_week(value: str, inputs: RuntimeInputs) -> int | None:
    text = str(value or "")
    match = re.search(r"(\d{1,2})\s*(?:周|w|week)", text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    date_match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if not date_match:
        return None
    due_date = _safe_date(int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3)))
    if due_date is None:
        return None
    today = _message_date(inputs)
    gestational_days = 280 - (due_date - today).days
    if gestational_days < 0:
        return None
    return max(1, min(42, gestational_days // 7))


def _message_date(inputs: RuntimeInputs) -> date:
    raw = str(inputs.get("message_sent_at") or "")
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    return date.today()


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _normalize_hospital_bag_form_value(field_id: str, value: str) -> str:
    if field_id == "birth_path":
        return _normalize_birth_path(value) or value
    if field_id == "feeding_intention":
        return _normalize_feeding_intention(value) or value
    if field_id == "first_birth":
        return _normalize_first_birth(value) or value
    return value


def _normalize_birth_plan_form_value(field_id: str, value: Any) -> Any:
    if field_id == "birth_path":
        normalized = _normalize_birth_path(_first_text(value))
        if normalized == "未确定":
            return "还没确定"
        return normalized or _first_text(value)
    if field_id == "first_birth":
        normalized = _normalize_first_birth(_first_text(value))
        if normalized == "不确定":
            return "还没确定"
        return normalized or _first_text(value)
    if field_id == "feeding_intention":
        return _normalize_birth_plan_feeding_intention(_first_text(value))
    if isinstance(value, list):
        return [str(item).strip() for item in value if _has_meaningful_value(item)]
    return _first_text(value)


def _normalize_birth_plan_feeding_intention(value: str) -> str:
    feeding = _normalize_feeding_intention(value)
    if feeding == "母乳":
        return "母乳喂养"
    if feeding == "混合":
        return "母乳和配方奶都可能"
    if feeding == "配方":
        return "配方奶"
    if feeding == "未确定":
        return "还没想好"
    return value


def _normalize_feeding_intention(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if any(token in text for token in ("混合", "combo", "mixed")):
        return "混合"
    if any(token in text for token in ("配方", "奶粉", "formula")):
        return "配方"
    if any(token in text for token in ("母乳", "亲喂", "breast")):
        return "母乳"
    if text in {"未确定", "不确定", "还没想好", "unknown"}:
        return "未确定"
    return value


def _normalize_first_birth(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"是", "第一胎", "一胎", "first", "yes", "true"}:
        return "是"
    if text in {"否", "不是", "二胎", "多胎", "no", "false"}:
        return "否"
    if text:
        return "不确定" if text in {"不确定", "未知", "还没确定", "unknown"} else value
    return ""


def _split_hospital_provided_items(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_normalize_hospital_provided_item(item) for item in value if _normalize_hospital_provided_item(item)]
    text = str(value or "").strip()
    if not text:
        return []
    parts = re.split(r"[、,，;；\n]+", text)
    return [_normalize_hospital_provided_item(part) for part in parts if _normalize_hospital_provided_item(part)]


def _normalize_hospital_provided_item(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"^(医院|院方|病区|产房|月子中心)?\s*(会|已|已经|可能)?\s*(提供|准备|配有|有)\s*", "", text)
    return text.strip()


def _filter_provided_items(items: list[dict[str, Any]], provided_items: list[str]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for item in items:
        if _is_hospital_provided(item.get("label"), provided_items):
            continue
        filtered.append(item)
    return filtered


def _is_hospital_provided(label: Any, provided_items: list[str]) -> bool:
    label_text = str(label or "")
    if not label_text or not provided_items:
        return False
    label_key = _item_match_key(label_text)
    for provided in provided_items:
        provided_key = _item_match_key(provided)
        if not provided_key:
            continue
        if provided_key in label_key or label_key in provided_key:
            return True
        for alias in HOSPITAL_BAG_PROVIDED_ITEM_ALIASES.get(provided_key, ()):
            alias_key = _item_match_key(alias)
            if alias_key and (alias_key in label_key or label_key in alias_key):
                return True
    return False


def _item_match_key(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _support_person_needs_bag(context: dict[str, Any]) -> bool:
    value = str(context.get("support_person") or "")
    return not any(token in value for token in ("暂时没有", "不需要", "没有"))


def _postpartum_pad_quantity(context: dict[str, Any]) -> str:
    if context.get("birth_path") == "剖宫产" or str(context.get("expected_stay")) in {"4 天或以上"}:
        return "20片左右"
    return "10-20片"


def _limit_items(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return items[:limit]


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _normalized_placeholder(value: str) -> str:
    return str(value or "").strip().lower()


def _without_field_help_text(field: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in field.items() if key != "help_text"}


def _sanitize_birth_plan_form_field(field: dict[str, Any]) -> dict[str, Any]:
    if field.get("type") != "multi_select":
        return field
    options = field.get("options")
    if not isinstance(options, list):
        return field
    sanitized = [option for option in options if str(option) not in EXCLUSIVE_BIRTH_PLAN_MULTI_SELECT_OPTIONS]
    return {**field, "options": sanitized}


def create_card(args: dict[str, Any], inputs: RuntimeInputs) -> dict[str, Any]:
    card_json = args.get("card_json", {})
    if not isinstance(card_json, dict):
        card_json = {}
    card_type = args.get("card_type", "")
    assistant_followup = None
    if card_type == "hospital_bag_card":
        assistant_followup = _prepare_hospital_bag_card(card_json, inputs)
    elif card_type == "birth_plan_card":
        assistant_followup = _prepare_birth_plan_card(card_json, inputs)

    result = {
        "tool_name": "ui_card_create",
        "status": "card_created",
        "card": {
            "card_type": card_type,
            "schema_version": args.get("schema_version", ""),
            "card_json": card_json,
        },
    }
    if assistant_followup:
        result["assistant_followup"] = assistant_followup
    return result


def _prepare_hospital_bag_card(card_json: dict[str, Any], inputs: RuntimeInputs) -> dict[str, str] | None:
    title = str(card_json.get("title") or "").strip()
    if not title or title in {"待产包卡片", "Hospital Bag Card"}:
        card_json["title"] = "待产包"
    elif "待产包卡片" in title:
        card_json["title"] = title.replace("待产包卡片", "待产包")

    groups = card_json.get("packing_groups")
    if not isinstance(groups, list):
        groups = []
        card_json["packing_groups"] = groups
    _normalize_hospital_bag_scene_groups(groups)

    if _formula_only_feeding_intention(inputs):
        return dict(HOSPITAL_BAG_CART_ASSISTANT_FOLLOWUP)

    group = _find_lactation_or_postpartum_group(groups)
    if group is None:
        group = {"group_id": "postpartum_home_first_week", "title": "产后回家第一周用品", "items": []}
        groups.append(group)

    items = group.get("items")
    if not isinstance(items, list):
        items = []
        group["items"] = items

    _ensure_breast_pump_visible(items)

    return dict(HOSPITAL_BAG_CART_ASSISTANT_FOLLOWUP)


def _prepare_birth_plan_card(card_json: dict[str, Any], inputs: RuntimeInputs, form_data_override: dict[str, Any] | None = None) -> dict[str, str]:
    source = dict(card_json)
    form_data = form_data_override if form_data_override is not None else _confirmed_form_data(inputs)
    owner = _dict_value(source.get("owner"))
    overview = _dict_value(source.get("overview"))
    birth_preferences = _dict_value(source.get("birth_preferences"))
    plan_change_values = _birth_plan_change_values(source.get("if_plans_change"))
    top_priorities = _normalize_birth_plan_items(
        source.get("top_priorities"),
        _nested_text(source, "if_plans_change", "what_matters_most"),
        form_data.get("top_priorities"),
        form_data.get("priority_notes"),
        max_items=BIRTH_PLAN_CARD_SECTION_ITEM_LIMIT,
    )

    compact = {
        "card_type": "birth_plan_card",
        "schema_version": "1.0",
        "title": _localized_birth_plan_title(_first_text(source.get("title"))) or "分娩沟通卡",
        "subtitle": _localized_birth_plan_subtitle(_first_text(source.get("subtitle"))) or "产房沟通优先级卡片",
        "overview": {
            "due_date_or_week": _first_text(
                overview.get("due_date_or_week"),
                owner.get("due_date_or_week"),
                form_data.get("due_date_or_week"),
            )
            or "待确认",
            "birth_path": _normalize_birth_path(
                _first_text(
                    overview.get("birth_path"),
                    birth_preferences.get("birth_path"),
                    form_data.get("birth_path"),
                )
            )
            or "待确认",
            "birth_setting": _first_text(
                overview.get("birth_setting"),
                owner.get("birth_setting"),
                form_data.get("birth_setting"),
            ),
            "support_people": _first_text(
                overview.get("support_people"),
                overview.get("support_person"),
                owner.get("support_people"),
                owner.get("support_person"),
                form_data.get("support_person"),
                form_data.get("support_people"),
            )
            or "待确认",
        },
        "personalized_notes": _birth_plan_personalized_notes(form_data),
        "top_priorities": top_priorities,
        "communication": _normalize_birth_plan_items(
            source.get("communication"),
            source.get("communication_preferences"),
            form_data.get("communication_preferences"),
            _birth_plan_priority_communication_items(top_priorities),
            _birth_plan_priority_general_items(top_priorities),
            max_items=BIRTH_PLAN_CARD_SECTION_ITEM_LIMIT,
        ),
        "labor_preferences": _normalize_birth_plan_items(
            source.get("labor_preferences"),
            form_data.get("labor_preferences"),
            max_items=BIRTH_PLAN_CARD_SECTION_ITEM_LIMIT,
        ),
        "intervention_preferences": _normalize_birth_plan_items(
            source.get("intervention_preferences"),
            form_data.get("intervention_preferences"),
            _birth_plan_priority_intervention_items(top_priorities),
            max_items=BIRTH_PLAN_CARD_SECTION_ITEM_LIMIT,
        ),
        "pain_relief": _normalize_birth_plan_items(
            source.get("pain_relief"),
            source.get("pain_relief_preferences"),
            form_data.get("pain_relief_preferences"),
            form_data.get("pain_relief_notes"),
            max_items=BIRTH_PLAN_CARD_SECTION_ITEM_LIMIT,
        ),
        "baby_after_birth": _normalize_birth_plan_items(
            source.get("baby_after_birth"),
            source.get("baby_after_birth_preferences"),
            form_data.get("baby_after_birth_preferences"),
            _birth_plan_priority_baby_items(top_priorities),
            _birth_plan_feeding_note(form_data.get("feeding_intention")),
            max_items=BIRTH_PLAN_CARD_SECTION_ITEM_LIMIT,
        ),
        "if_plans_change": _normalize_birth_plan_items(
            *plan_change_values,
            form_data.get("if_plans_change"),
            max_items=BIRTH_PLAN_CARD_SECTION_ITEM_LIMIT,
        ),
        "emergency_authorization": _normalize_birth_plan_items(
            source.get("emergency_authorization"),
            form_data.get("emergency_authorization"),
            max_items=BIRTH_PLAN_CARD_SECTION_ITEM_LIMIT,
        ),
        "questions_for_hospital": _normalize_birth_plan_items(
            source.get("questions_for_hospital"),
            form_data.get("hospital_questions_focus"),
            _birth_plan_default_questions(form_data),
            max_items=BIRTH_PLAN_CARD_SECTION_ITEM_LIMIT,
        ),
        "medical_notes": _normalize_medical_notes(form_data.get("medical_notes")),
        "disclaimer": _localized_disclaimer(_first_text(source.get("disclaimer"))) or BIRTH_PLAN_DISCLAIMER,
    }

    card_json.clear()
    card_json.update(compact)
    return dict(BIRTH_PLAN_ASSISTANT_FOLLOWUP)


def _find_lactation_or_postpartum_group(groups: list[Any]) -> dict[str, Any] | None:
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("group_id", "")).lower()
        title = str(group.get("title", "")).lower()
        if any(token in group_id for token in ("lactation", "breastfeeding", "feeding", "postpartum")):
            return group
        if any(token in title for token in ("lactation", "breastfeeding", "feeding", "postpartum", "哺乳", "喂养", "产后")):
            return group
    return None


def _normalize_hospital_bag_scene_groups(groups: list[Any]) -> None:
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("group_id", "")).lower()
        title = str(group.get("title", ""))
        text = f"{group_id} {title}".lower()
        if any(token in text for token in ("lactation", "breastfeeding", "feeding", "postpartum", "哺乳", "喂养", "产后回家", "产后护理")):
            group["group_id"] = "postpartum_home_first_week"
            group["title"] = "产后回家第一周用品"
        elif any(token in text for token in ("documents", "certificate", "证件", "资料", "文件")):
            group["group_id"] = "documents"
            group["title"] = "证件文件包"
        elif any(token in text for token in ("baby", "宝宝", "新生儿")):
            group["group_id"] = "baby_discharge_bag"
            group["title"] = "宝宝出院包"
        elif any(token in text for token in ("support", "partner", "companion", "陪产", "支持人")):
            group["group_id"] = "support_person_bag"
            group["title"] = "陪产人包"
        elif any(token in text for token in ("car", "travel", "traffic", "transport", "车上", "交通", "停车", "路线")):
            group["group_id"] = "car_backup_bag"
            group["title"] = "车上备用包"
        elif any(token in text for token in ("mom", "mother", "communication", "food", "妈妈", "衣物", "清洁", "护理", "通讯", "饮食", "住院")):
            group["group_id"] = "mom_hospital_bag"
            group["title"] = "妈妈住院包"


def _ensure_breast_pump_visible(items: list[Any]) -> None:
    pump_index = _breast_pump_item_index(items)
    target_index = min(2, len(items))
    if pump_index is None:
        items.insert(target_index, dict(PUMP_ITEM))


def _breast_pump_item_index(items: list[Any]) -> int | None:
    for index, item in enumerate(items):
        text = json.dumps(item, ensure_ascii=False).lower() if isinstance(item, dict) else str(item).lower()
        if "吸奶" in text or "breast pump" in text or "pump" in text:
            return index
    return None


def _formula_only_feeding_intention(inputs: RuntimeInputs) -> bool:
    data = _confirmed_form_data(inputs)
    if data:
        feeding_intention = str(data.get("feeding_intention", "")).strip().lower()
        return feeding_intention in {"配方", "formula", "formula feeding"}
    message = str(inputs.get("user_message", ""))
    marker = "confirmed_form_data:"
    if marker not in message:
        return False
    raw_json = message.split(marker, 1)[1].strip()
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return False
    feeding_intention = str(data.get("feeding_intention", "")).strip().lower()
    return feeding_intention in {"配方", "formula", "formula feeding"}


def _confirmed_form_data(inputs: RuntimeInputs) -> dict[str, Any]:
    message = str(inputs.get("user_message", ""))
    marker = "confirmed_form_data:"
    if marker not in message:
        return {}
    raw_json = message.split(marker, 1)[1].strip()
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, list):
            text = ", ".join(str(item) for item in value if _has_meaningful_value(item))
        elif isinstance(value, dict):
            text = ", ".join(
                str(nested_value)
                for nested_value in value.values()
                if _has_meaningful_value(nested_value)
            )
        else:
            text = str(value or "").strip()
        if _has_meaningful_value(text):
            return text
    return ""


def _localized_disclaimer(value: str) -> str:
    if not value:
        return ""
    normalized = value.strip().lower()
    if "this card is for communication only" in normalized or "clinician and hospital guidance" in normalized:
        return BIRTH_PLAN_DISCLAIMER
    return value


def _localized_birth_plan_title(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"birth plan card", "labor room communication priority card"}:
        return "分娩沟通卡"
    return value


def _localized_birth_plan_subtitle(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"birth plan card", "labor room communication priority card"}:
        return "产房沟通优先级卡片"
    return value


def _normalize_birth_path(value: str) -> str:
    text = value.strip()
    if not _has_meaningful_value(text):
        return ""
    return BIRTH_PATH_ALIASES.get(text.lower()) or BIRTH_PATH_ALIASES.get(text) or text


def _nested_text(source: dict[str, Any], *keys: str) -> str:
    current: Any = source
    for key in keys:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return _first_text(current)


def _birth_plan_change_values(value: Any) -> list[Any]:
    if not isinstance(value, dict):
        return [value]
    return [value.get("how_to_explain_changes"), value.get("who_should_be_involved")]


def _birth_plan_priority_communication_items(items: list[str]) -> list[str]:
    mapped: list[str] = []
    for item in items:
        if "伴侣" in item or "支持人" in item:
            mapped.append("重要决定也请同步伴侣/支持人")
        if "鼓励" in item or "反馈" in item:
            mapped.append("希望团队主动给我反馈和鼓励")
        if "减少不必要的干预" in item or "不必要的干预" in item or "非必要操作" in item:
            mapped.append("干预前请先和我沟通必要性")
    return mapped


def _birth_plan_priority_baby_items(items: list[str]) -> list[str]:
    mapped: list[str] = []
    for item in items:
        if "肌肤接触" in item or "抱一抱" in item or "贴一贴" in item:
            mapped.append("出生后希望尽早肌肤接触")
        if "尝试母乳" in item or "尽早母乳" in item or "喂母乳" in item or "亲喂" in item:
            mapped.append("希望尽早尝试母乳")
    return mapped


def _birth_plan_priority_intervention_items(items: list[str]) -> list[str]:
    mapped: list[str] = []
    for item in items:
        if any(token in item for token in ("侧切", "产钳", "真空吸引", "人工破膜")):
            mapped.append(item)
    return mapped


def _birth_plan_priority_general_items(items: list[str]) -> list[str]:
    mapped: list[str] = []
    for item in items:
        if any(token in item for token in ("肌肤接触", "抱一抱", "贴一贴", "尝试母乳", "尽早母乳", "喂母乳", "亲喂", "侧切", "产钳", "真空吸引", "人工破膜")):
            continue
        if any(token in item for token in ("伴侣", "支持人", "鼓励", "反馈", "减少不必要的干预", "不必要的干预", "非必要操作")):
            continue
        mapped.append(f"希望医护团队知道：{item}")
    return mapped


def _normalize_birth_plan_items(*values: Any, max_items: int) -> list[str]:
    items: list[str] = []
    for value in values:
        for item in _flatten_text_items(value):
            softened = _soften_birth_plan_request(_normalize_birth_plan_text(item))
            if softened and softened not in items:
                items.append(softened)
            if len(items) >= max_items:
                return items
    return items


def _normalize_medical_notes(value: Any) -> list[str]:
    # Medical notes must remain user-supplied facts only; do not derive them from model-generated card fields.
    return _flatten_text_items(value)[:3]


def _birth_plan_personalized_notes(form_data: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    due = _first_text(form_data.get("due_date_or_week"))
    birth_path = _normalize_birth_path(_first_text(form_data.get("birth_path")))
    birth_setting = _first_text(form_data.get("birth_setting"))
    support_people = _first_text(form_data.get("support_person"), form_data.get("support_people"))
    first_birth = _first_text(form_data.get("first_birth"))
    if due or birth_path:
        parts = [part for part in (due, birth_path) if part]
        notes.append(f"基于{'、'.join(parts)}，整理成适合产检或入院沟通的重点卡片。")
    if first_birth == "是":
        notes.append("已按第一胎更需要解释、反馈和陪伴的场景整理。")
    elif first_birth == "否":
        notes.append("已按非第一胎保留更关键的沟通重点。")
    if support_people:
        notes.append(f"已把{support_people}作为重要沟通参与人。")
    if birth_setting:
        notes.append(f"可在{birth_setting}产检或入院前给医护团队查看。")
    return notes[:3]


def _birth_plan_default_questions(form_data: dict[str, Any]) -> list[str]:
    questions = [
        "陪产/支持人：确认谁可以参与沟通、陪产或术前/产房决策。",
    ]
    if _normalize_birth_path(_first_text(form_data.get("birth_path"))) == "剖宫产":
        questions.append("术后接触宝宝/喂养：确认安全允许时的肌肤接触、喂养和宝宝护理流程。")
    else:
        questions.append("疼痛缓解：确认无痛或麻醉什么时候可以沟通、有哪些选择。")
    return questions


def _birth_plan_feeding_note(value: Any) -> str:
    text = _first_text(value)
    if not _has_meaningful_value(text):
        return ""
    return f"喂养意向：{text}"


def _normalize_birth_plan_text(text: str) -> str:
    normalized = re.sub(r"^\s*\d+[.)、．]\s*", "", text.strip())
    normalized = re.sub(r"\s+", " ", normalized)
    lowered = normalized.lower()
    if lowered in {"skin-to-skin", "skin to skin"}:
        return "出生后尽早肌肤接触"
    if normalized == "我还没想好，请帮我整理成温和版本":
        return "希望医护团队在关键步骤前先解释，并给我一点时间确认。"
    return normalized.replace("skin-to-skin", "出生后尽早肌肤接触").replace("skin to skin", "出生后尽早肌肤接触")


def _flatten_text_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            items.extend(_flatten_text_items(item))
        return items
    if isinstance(value, dict):
        items = []
        for item in value.values():
            items.extend(_flatten_text_items(item))
        return items
    text = str(value).strip()
    if not _has_meaningful_value(text):
        return []

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for token in ("；", ";", "\n"):
        normalized = normalized.replace(token, "\n")
    items = []
    for raw_item in normalized.split("\n"):
        item = raw_item.strip(" -•、，,。.")
        if _has_meaningful_value(item):
            items.append(item)
    return items


def _has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_has_meaningful_value(item) for item in value)
    text = str(value).strip()
    return text.lower() not in PLACEHOLDER_VALUES


def _soften_birth_plan_request(text: str) -> str:
    stripped = text.strip()
    lowered = stripped.lower()
    english_tokens = ("i refuse", "must", "never", "do not", "don't")
    chinese_tokens = ("拒绝", "不要", "不允许", "必须", "一定要")
    strong_tokens = (*english_tokens, *chinese_tokens)
    if not any(token in lowered or token in stripped for token in strong_tokens):
        return stripped

    if "侧切" in stripped:
        return "如果需要侧切，请先说明原因并和我沟通。"
    if "产钳" in stripped or "真空吸引" in stripped or "吸引" in stripped:
        return "如果需要产钳或吸引，请先解释原因并和我沟通。"
    if "人工破水" in stripped or "破水" in stripped:
        return "如果需要人工破水，请先说明原因并和我沟通。"
    if "灌肠" in stripped or "剃毛" in stripped:
        return "如果需要灌肠或剃毛，请先告诉我是否必须。"
    if "无痛" in stripped or "硬膜外" in stripped or "麻醉" in stripped:
        return "关于无痛或麻醉选择，请先说明可选方案、时机和注意事项。"

    softened = stripped
    for token in strong_tokens:
        softened = softened.replace(token, "").replace(token.capitalize(), "")
    softened = softened.strip(" ，,。.:：;；")
    if not softened:
        softened = "这项偏好"
    return f"在{softened}前，请先和我沟通。"


def _normalize_form_fields(raw_fields: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_fields, list):
        return []

    fields: list[dict[str, Any]] = []
    for raw_field in raw_fields:
        field = raw_field
        if isinstance(raw_field, str):
            try:
                parsed = json.loads(raw_field)
            except json.JSONDecodeError:
                continue
            field = parsed
        if not isinstance(field, dict):
            continue

        normalized = {
            "id": str(field.get("id", "")),
            "label": str(field.get("label", "")),
            "type": str(field.get("type", "text")),
            "required": bool(field.get("required", False)),
        }
        for optional_key in ("help_text", "placeholder", "default_value"):
            value = field.get(optional_key)
            if value is not None and value != "":
                normalized[optional_key] = value
        options = field.get("options")
        if isinstance(options, list) and options:
            normalized["options"] = [str(option) for option in options]
        if normalized["id"] and normalized["label"]:
            fields.append(normalized)

    return fields
