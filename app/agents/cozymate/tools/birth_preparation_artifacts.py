from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from .hospital_bag_flow import build_hospital_bag_card_json, build_hospital_bag_followup
from .pump_models import find_pump_product


HOSPITAL_BAG_CART_URL = "/hospital-bag-cart"
HOSPITAL_BAG_CART_LINK = f"[打开待产包购物车]({HOSPITAL_BAG_CART_URL})"
HOSPITAL_BAG_DISCLAIMER = "请优先遵循医院要求和医生/助产士的具体指导。"
HOSPITAL_BAG_CART_USD_TO_CNY_RATE = 6.8
HOSPITAL_BAG_CART_PRODUCT_IMAGE_URLS = {
    "milk-pump": "https://momcozy.com/cdn/shop/files/MomcozyMoblieFlow_BreastPump_7.jpg?v=1776163451",
    "mom-pad": "https://momcozy.com/cdn/shop/files/01_e3747022-14ff-4a12-a503-044276f89265.webp?v=1779352318",
    "mom-sanitary": "https://momcozy.com/cdn/shop/files/01_e3747022-14ff-4a12-a503-044276f89265.webp?v=1779352318",
    "mom-underwear": "https://momcozy.com/cdn/shop/files/PK006-1_-1.png?v=1779351545",
    "mom-wipes": "https://momcozy.com/cdn/shop/files/12b.jpg?v=1779353160",
    "mom-bottle": "https://momcozy.com/cdn/shop/files/01_e3747022-14ff-4a12-a503-044276f89265.webp?v=1779352318",
    "mom-briefs": "https://momcozy.com/cdn/shop/files/b58ce7b99582c961375527c3c6b27ebb_9023fff7-0f2e-42c1-864e-fd2a7a732e72.png?v=1779351709",
    "baby-diaper": "https://babycozy.com/cdn/shop/files/1.1_8e53efbf-da7d-4997-a472-3a335bedbf31.jpg?v=1711696033",
    "baby-wipes": "https://momcozy.com/cdn/shop/files/12b.jpg?v=1779353160",
    "baby-towel": "https://momcozy.com/cdn/shop/files/1_d4e469a4-e733-4cf1-8863-c3c5256eca2a.jpg?v=1779353358",
    "baby-blanket": "https://momcozy.com/cdn/shop/files/1-1_abb6b92a-ac07-4b73-b3a5-7d995989b721.jpg?v=1779353389",
    "baby-blanket-basic": "https://momcozy.com/cdn/shop/files/1_7732d4e1-f5a7-4a75-8f7e-c888eed37548.jpg?v=1779352173",
    "baby-clothes": "https://momcozy.com/cdn/shop/files/1_f348eb24-2845-4b7b-8cff-6f78510fa1fa.webp?v=1779422192",
    "baby-bath-towel": "https://momcozy.com/cdn/shop/files/yujin.jpg?v=1779353668",
    "milk-pad": "https://momcozy.com/cdn/shop/files/5_dbb18aeb-7e96-4c95-a1a7-5a9378875b7b.jpg?v=1779352202",
    "milk-cream": "https://momcozy.com/cdn/shop/files/MomcozyNippleCreramforBreastfeeding_8.jpg?v=1779352434",
    "milk-storage": "https://momcozy.com/cdn/shop/files/13_5b267527-b032-4c66-bec8-21c675a15007.webp?v=1779430748",
    "milk-bra": "https://momcozy.com/cdn/shop/files/yn21_ae9a5331-7afc-4dde-abc1-5136e356bcbe.jpg?v=1736236303",
    "milk-bottle": "https://momcozy.com/cdn/shop/files/619nKXnpNKL._SL1500.jpg?v=1779354235",
}

HOSPITAL_BAG_FORM_FIELDS: list[dict[str, Any]] = [
    {
        "id": "due_date_or_week",
        "label": "基本信息｜预产期或当前孕周",
        "type": "text",
        "required": True,
        "placeholder": "例如：2026-06-12 或 37 周",
    },
    {"id": "first_birth", "label": "基本信息｜是否第一胎", "type": "select", "required": True, "options": ["是", "否"]},
    {
        "id": "fetus_count",
        "label": "基本信息｜这次是单胎、双胎，还是三胎及以上？",
        "type": "select",
        "required": True,
        "options": ["单胎", "双胎", "三胎及以上", "不确定"],
    },
    {
        "id": "pregnancy_history_or_notes",
        "label": "基本信息｜医生是否提示过特殊情况",
        "type": "multi_select",
        "required": True,
        "allow_other_input": True,
        "other_placeholder": "请简单填写医生提示的情况",
        "options": ["没有", "妊娠糖尿病", "血压或子痫前期风险", "胎盘问题", "早产风险", "宝宝可能 NICU", "其它"],
    },
    {"id": "birth_path", "label": "生产信息｜分娩方式", "type": "select", "required": True, "options": ["顺产", "剖宫产", "还不确定"]},
    {
        "id": "feeding_intention",
        "label": "喂养信息｜喂养意向",
        "type": "select",
        "required": True,
        "options": ["亲喂母乳", "配方奶", "混合喂养", "还不确定"],
    },
    {
        "id": "return_to_work_timing",
        "label": "喂养信息｜产后多久返工",
        "type": "text",
        "required": True,
        "placeholder": "例如：6 周后、3 个月后、暂不返工",
    },
    {
        "id": "support_person",
        "label": "照护信息｜产后前两周支持情况",
        "type": "select",
        "required": True,
        "options": ["有人全天帮忙", "白天主要自己", "夜间主要自己", "支持少", "不确定"],
    },
    {
        "id": "top_worries",
        "label": "偏好信息｜最焦虑的事",
        "type": "multi_select",
        "required": True,
        "allow_other_input": True,
        "other_placeholder": "请简单写下你最担心的事",
        "options": ["不知道什么时候去医院", "怕漏买", "怕母乳不够", "怕剖宫产恢复", "怕产后没人帮", "怕宝宝用品准备不全", "其它"],
    },
]

HOSPITAL_BAG_REQUIRED_LABELS = {
    "due_date_or_week": "预产期或当前孕周",
    "first_birth": "是否第一胎",
    "fetus_count": "本次妊娠胎数",
    "pregnancy_history_or_notes": "医生是否提示过特殊情况",
    "birth_path": "分娩方式",
    "feeding_intention": "喂养意向",
    "return_to_work_timing": "产后多久返工",
    "support_person": "产后前两周支持情况",
    "top_worries": "最焦虑的事",
}

DEFAULT_HOSPITAL_BAG_CART_GROUPS: list[dict[str, Any]] = [
    {
        "title": "妈妈护理",
        "tone": "rose",
        "items": [
            {"id": "mom-pad", "name": "产褥垫组合装", "desc": "入院与产后前几天使用", "qty": 1, "price": 59.9, "keywords": ["产褥垫", "护理垫"]},
            {"id": "mom-sanitary", "name": "产妇卫生巾", "desc": "夜用加长款，按住院天数准备", "qty": 1, "price": 39.9, "keywords": ["卫生巾"]},
            {"id": "mom-underwear", "name": "一次性内裤", "desc": "高腰柔软，产后更方便更换", "qty": 1, "price": 49.9, "keywords": ["内裤", "一次性内裤"]},
            {"id": "mom-wipes", "name": "产后护理湿巾", "desc": "温和清洁，适合住院随身包", "qty": 1, "price": 29.9, "keywords": ["湿巾", "护理湿巾"]},
            {"id": "mom-bottle", "name": "产后冲洗瓶", "desc": "产后清洁更方便，是否带去医院按医院建议", "qty": 1, "price": 39.9, "keywords": ["冲洗瓶"]},
            {"id": "mom-briefs", "name": "高腰收腹内裤", "desc": "不压腹，更适合产后恢复期穿着", "qty": 1, "price": 69.9, "keywords": ["收腹", "高腰"]},
        ],
    },
    {
        "title": "宝宝出院",
        "tone": "mint",
        "items": [
            {"id": "baby-diaper", "name": "新生儿纸尿裤", "desc": "NB 码小包装，避免带太多", "qty": 1, "price": 59.9, "keywords": ["纸尿裤", "尿不湿"]},
            {"id": "baby-wipes", "name": "婴儿柔湿巾", "desc": "无香精，适合换尿裤场景", "qty": 1, "price": 29.9, "keywords": ["婴儿湿巾", "柔湿巾"]},
            {"id": "baby-towel", "name": "棉柔巾", "desc": "洗脸、擦手、护理都可用", "qty": 1, "price": 29.9, "keywords": ["棉柔巾"]},
            {"id": "baby-blanket", "name": "宝宝出院包被", "desc": "柔软包裹，按季节搭配外层", "qty": 1, "price": 129.0, "keywords": ["包被"]},
            {"id": "baby-clothes", "name": "新生儿连体衣礼盒", "desc": "出院和回家第一周可替换穿", "qty": 1, "price": 159.0, "keywords": ["连体衣", "衣服", "礼盒"]},
            {"id": "baby-bath-towel", "name": "婴儿浴巾", "desc": "洗澡、包裹和保暖都可用", "qty": 1, "price": 59.9, "keywords": ["浴巾"]},
        ],
    },
    {
        "title": "母乳喂养",
        "tone": "sky",
        "items": [
            {"id": "milk-pad", "name": "防溢乳垫", "desc": "母乳或混合喂养可先备小包装", "qty": 1, "price": 39.9, "keywords": ["防溢乳垫", "乳垫"]},
            {"id": "milk-cream", "name": "乳头护理霜", "desc": "哺乳初期不适时可咨询后使用", "qty": 1, "price": 49.9, "keywords": ["乳头霜", "护理霜"]},
            {"id": "milk-storage", "name": "储奶袋", "desc": "返家后储奶备用，住院可少量准备", "qty": 1, "price": 49.9, "keywords": ["储奶袋"]},
            {"id": "milk-pump", "name": "便携式吸奶器", "desc": "可选备用项，是否带去医院先问医院", "qty": 1, "price": 699.0, "keywords": ["吸奶器"]},
            {"id": "milk-bra", "name": "哺乳文胸", "desc": "产后和哺乳初期更舒适", "qty": 1, "price": 159.0, "keywords": ["哺乳文胸", "文胸"]},
            {"id": "milk-bottle", "name": "宽口径奶瓶", "desc": "混合喂养或返家后备用", "qty": 1, "price": 89.9, "keywords": ["奶瓶"]},
        ],
    },
]
HOSPITAL_BAG_CART_PUMP_ITEM_ID = "milk-pump"
HOSPITAL_BAG_CART_BUDGET_REMOVE_ORDER = [
    "baby-clothes",
    "mom-briefs",
    "milk-bra",
    "milk-storage",
    "baby-bath-towel",
    "mom-bottle",
    "milk-bottle",
    "milk-cream",
    "mom-wipes",
    "milk-pad",
    "baby-towel",
]
HOSPITAL_BAG_CART_LIGHT_BUDGET_REMOVE_IDS = {"baby-clothes", "mom-briefs", "milk-bra"}
HOSPITAL_BAG_CART_PROTECTED_ITEM_IDS = {
    "mom-pad",
    "mom-sanitary",
    "mom-underwear",
    "baby-diaper",
    "baby-wipes",
    "baby-blanket",
    "baby-blanket-basic",
    HOSPITAL_BAG_CART_PUMP_ITEM_ID,
}
HOSPITAL_BAG_CART_BUDGET_REPLACEMENTS: dict[str, dict[str, Any]] = {
    "baby-blanket": {
        "id": "baby-blanket-basic",
        "name": "基础款宝宝包被",
        "desc": "先选基础款，按季节再加外层",
        "qty": 1,
        "price": 59.9,
        "keywords": ["包被"],
    }
}
HOSPITAL_BAG_CART_REPLACEMENT_ORIGINAL_BY_ID = {
    replacement["id"]: original_id for original_id, replacement in HOSPITAL_BAG_CART_BUDGET_REPLACEMENTS.items()
}


def create_birth_preparation_artifact_result(
    tool_name: str,
    args: dict[str, Any],
    *,
    pump_products: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if tool_name == "hospital_bag_form_create":
        return hospital_bag_form_result(args)
    if tool_name == "hospital_bag_card_create":
        return hospital_bag_card_result(args)
    if tool_name == "hospital_bag_cart_mutate":
        return hospital_bag_cart_update_result(args, pump_products=pump_products)
    raise ValueError(f"Unsupported birth-preparation artifact tool: {tool_name}")


def artifact_record_from_birth_preparation_result(result: dict[str, Any]) -> dict[str, Any] | None:
    status = _text(result.get("status"))
    if status not in {"form_created", "card_created", "cart_updated", "cart_unchanged"}:
        return None
    form = _dict(result.get("form"))
    if form:
        return {
            "artifact_type": "form",
            "schema_version": "1.0",
            "payload": {"tool_name": result.get("tool_name"), "form": form},
        }
    card = _dict(result.get("card"))
    if card:
        card_json = _dict(card.get("card_json"))
        payload = {
            "tool_name": result.get("tool_name"),
            "card": card,
            "card_json": card_json,
            "assistant_followup": result.get("assistant_followup"),
        }
        source_form_submission_id = _text(result.get("source_form_submission_id"))
        if source_form_submission_id:
            payload["source_form_submission_id"] = source_form_submission_id
        return {
            "artifact_type": _text(card.get("card_type")) or _text(card_json.get("card_type")) or "card",
            "schema_version": _text(card.get("schema_version")) or _text(card_json.get("schema_version")) or "1.0",
            "payload": payload,
        }
    cart_update = _dict(result.get("cart_update"))
    if cart_update:
        return {
            "artifact_type": "hospital_bag_cart",
            "schema_version": "1.0",
            "payload": {"tool_name": result.get("tool_name"), "cart_update": cart_update, "summary": result.get("summary")},
        }
    return None


def hospital_bag_form_result(args: dict[str, Any]) -> dict[str, Any]:
    default_values = _dict(args.get("default_values"))
    return {
        "tool_name": "ui_form_create",
        "status": "form_created",
        "form": {
            "id": "hospital_bag_intake",
            "title": "信息采集",
            "description": "",
            "submit_label": "提交",
            "fields": _fields_with_defaults(HOSPITAL_BAG_FORM_FIELDS, default_values),
            "default_values": _allowed_defaults(HOSPITAL_BAG_FORM_FIELDS, default_values),
        },
    }


def hospital_bag_card_result(args: dict[str, Any]) -> dict[str, Any]:
    result_tool_name = _text(args.get("runtime_result_tool_name")) or "hospital_bag_card_create"
    form_data = _confirmed_form_data(args)
    if not form_data:
        return _needs_context_result(
            result_tool_name,
            "needs_confirmed_form_data",
            "生成待产包清单前，需要先提交待产包信息采集表单。",
            list(HOSPITAL_BAG_REQUIRED_LABELS),
            "请先完成并提交待产包信息采集表单，我再根据确认后的信息整理待产包清单。",
        )
    missing = missing_hospital_bag_required_fields(form_data)
    if missing:
        labels = [HOSPITAL_BAG_REQUIRED_LABELS[field_id] for field_id in missing[:3]]
        return _needs_context_result(
            result_tool_name,
            "needs_required_form_fields",
            "生成待产包清单前，需要先补全待产包表单必填信息。",
            missing,
            f"待产包清单还不能生成，表单里还差{'、'.join(labels)}。请先补全并提交待产包信息采集表单。",
        )
    invalid = invalid_hospital_bag_required_fields(form_data)
    if invalid:
        labels = [HOSPITAL_BAG_REQUIRED_LABELS[field_id] for field_id in invalid[:3]]
        return _needs_context_result(
            result_tool_name,
            "needs_valid_form_fields",
            "生成待产包清单前，需要修正待产包表单中的无效信息。",
            invalid,
            f"待产包清单还不能生成，请重新确认{'、'.join(labels)}。",
        )
    card_json = _hospital_bag_card_json(
        form_data,
        generation_mode=_text(args.get("generation_mode")) or "standard",
    )
    return {
        "tool_name": result_tool_name,
        "status": "card_created",
        "card": {"card_type": "hospital_bag_card", "schema_version": "1.0", "card_json": card_json},
        "source_form_submission_id": _text(args.get("form_submission_id")),
        "assistant_followup": build_hospital_bag_followup(card_json),
    }


def missing_hospital_bag_required_fields(form_data: dict[str, Any]) -> list[str]:
    return [
        field_id
        for field_id in HOSPITAL_BAG_REQUIRED_LABELS
        if not _has_value(form_data.get(field_id))
    ]


def invalid_hospital_bag_required_fields(
    form_data: dict[str, Any],
) -> list[str]:
    fields_by_id = {
        str(field.get("id") or ""): field
        for field in HOSPITAL_BAG_FORM_FIELDS
    }
    invalid: list[str] = []
    for field_id in HOSPITAL_BAG_REQUIRED_LABELS:
        value = form_data.get(field_id)
        if not _has_value(value):
            continue
        field = fields_by_id[field_id]
        field_type = str(field.get("type") or "")
        if field_type == "text":
            valid = isinstance(value, str) and len(value.strip()) <= 500
        elif field_type == "select":
            valid = (
                isinstance(value, str)
                and value.strip() in set(field.get("options") or [])
            )
        elif field_type == "multi_select":
            valid = _valid_hospital_bag_multi_select(
                value,
                options=field.get("options"),
                allow_other=field.get("allow_other_input") is True,
            )
        else:
            valid = False
        if not valid:
            invalid.append(field_id)
    return invalid


def _valid_hospital_bag_multi_select(
    value: Any,
    *,
    options: Any,
    allow_other: bool,
) -> bool:
    if not isinstance(value, list) or not value or len(value) > 12:
        return False
    allowed = {
        str(item).strip()
        for item in options
        if isinstance(item, str) and item.strip()
    } if isinstance(options, list) else set()
    for item in value:
        if not isinstance(item, str):
            return False
        normalized = item.strip()
        if not normalized or len(normalized) > 500:
            return False
        if normalized in allowed and normalized not in {"其它", "其他"}:
            continue
        if allow_other and any(
            normalized.startswith(prefix) and normalized[len(prefix):].strip()
            for prefix in ("其它：", "其他：")
        ):
            continue
        return False
    return True


def build_birth_journey_plan_result(plan_context: dict[str, Any]) -> dict[str, Any]:
    due = _first_text(plan_context.get("due_date_or_week"), plan_context.get("current_week"), plan_context.get("due_date")) or "待确认"
    card_json = {
        "card_type": "birth_journey_plan_card",
        "schema_version": "1.0",
        "todo_engine_version": "legacy-compatible-1.0",
        "title": "孕期计划",
        "subtitle": "把现在到生产前后要做的事按阶段排清楚",
        "owner": {
            "due_date_or_week": due,
            "birth_path": _first_text(plan_context.get("birth_path"), plan_context.get("delivery_method")),
            "birth_setting": _first_text(plan_context.get("birth_setting"), plan_context.get("birth_hospital"), plan_context.get("hospital")),
            "support_person": _first_text(plan_context.get("support_person"), plan_context.get("support_people")),
            "feeding_intention": _first_text(plan_context.get("feeding_intention"), plan_context.get("feeding_plan")),
        },
        "todo_plan": {
            "periods": [
                {
                    "id": "current_stage",
                    "title": "当前阶段",
                    "subtitle": "先处理最影响安心感的事项",
                    "display_mode": "expanded",
                    "status": "current",
                    "items": [
                        _todo_item("confirm_checkup_questions", "整理下次产检要问的问题", "把最担心的 3-5 个问题写下来，产检时直接确认。"),
                        _todo_item("prepare_hospital_bag", "开始整理待产包", "先准备证件、妈妈住院用品和宝宝出院用品，不用一次买齐。"),
                        _todo_item("confirm_support_plan", "确认产后前两周支持安排", "确认谁陪产、谁接送、谁负责家务和夜间照护。"),
                    ],
                },
                {
                    "id": "labor_and_hospital",
                    "title": "临产与住院",
                    "subtitle": "把去医院和住院沟通提前准备好",
                    "display_mode": "collapsed",
                    "status": "upcoming",
                    "items": [
                        _todo_item("know_labor_signs", "确认需要去医院的信号", "向医院确认破水、出血、胎动减少、规律宫缩时的处理方式。"),
                    ],
                },
            ]
        },
        "generation_context": {"source": "agent", "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
        "next_action": {"label": "继续整理待产包", "send_text": "帮我整理一份个性化待产包清单"},
        "disclaimer": "这份计划用于准备和沟通，不能替代医生、助产士或医院的具体建议；有破水、出血、胎动明显减少、规律宫缩加密或明显不适时，请按医院或医生指导处理。",
    }
    owner = card_json.get("owner")
    if isinstance(owner, dict):
        card_json["owner"] = {key: value for key, value in owner.items() if _has_value(value)}
    return {
        "tool_name": "plan_mutate",
        "status": "card_created",
        "card": {"card_type": "birth_journey_plan_card", "schema_version": "1.0", "card_json": card_json},
        "plan": {"payload": card_json, "status": "active"},
    }


def hospital_bag_cart_update_result(
    args: dict[str, Any],
    *,
    pump_products: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    available_pump_products = pump_products or []
    action = _text(args.get("operation"))
    assistant_message = _text(args.get("assistant_message")) or _text(args.get("message")) or _text(args.get("summary"))
    item_ids = _string_list(args.get("item_ids"))
    preserve_item_ids = _string_list(args.get("preserve_item_ids"))
    current_groups = _cart_groups_from_args(args)

    if action in {"replace_pump_model", "add_pump_model"}:
        product = find_pump_product(available_pump_products, _text(args.get("product_sku_id")))
        if product is None:
            message = assistant_message or "你想换成哪一款 Momcozy 吸奶器？请告诉我具体型号。"
            return _cart_needs_clarification(message)
        next_groups, changed = _upsert_hospital_bag_pump_model(
            current_groups,
            product,
            pump_products=available_pump_products,
        )
        totals = _cart_totals(next_groups)
        if changed["mode"] == "unchanged":
            message = assistant_message or f"购物车里已经是「{product['name']}」了，我先不重复添加。"
        elif changed["mode"] == "added":
            message = assistant_message or f"好，我把「{product['name']}」加到母乳喂养里了，官方价折合约 {_pump_price_cny_label(product)}。"
        else:
            message = assistant_message or f"好，我把「{changed.get('from_name') or '原来的吸奶器'}」换成「{product['name']}」了，官方价折合约 {_pump_price_cny_label(product)}。"
        return _hospital_bag_cart_update_envelope(
            action,
            next_groups,
            totals,
            message,
            replaced_items=[changed] if changed["mode"] == "replaced" else [],
            restored_item_ids=[product["sku_id"]] if changed["mode"] == "added" else [],
            restored_item_names=[product["name"]] if changed["mode"] == "added" else [],
        )

    if action in {"optimize_budget", "apply_budget_plan"}:
        budget_result = _optimize_hospital_bag_cart_budget(
            current_groups,
            target_budget=_target_budget(args.get("target_budget")),
            budget_mode=_text(args.get("budget_mode")) or "cheaper",
            preference=_text(args.get("preference")) or "balanced",
            preserve_item_ids=preserve_item_ids,
            allow_remove_pump=bool(args.get("allow_remove_pump")),
        )
        message = assistant_message or _hospital_bag_budget_message(budget_result)
        return _hospital_bag_cart_update_envelope(
            "optimize_budget",
            budget_result["groups"],
            budget_result["totals"],
            message,
            before_totals=budget_result["before_totals"],
            removed_item_ids=budget_result["removed_item_ids"],
            removed_item_names=budget_result["removed_item_names"],
            replaced_items=budget_result["replaced_items"],
            target_budget=budget_result["target_budget"],
            budget_met=budget_result["budget_met"],
        )

    if action in {"remove_items", "mark_provided", "mark_owned"}:
        if not item_ids:
            return _cart_needs_clarification(assistant_message or _missing_item_message(action))
        next_groups, removed_names = _remove_hospital_bag_cart_items(current_groups, item_ids)
        if not removed_names:
            message = assistant_message or "我没有在当前购物车里找到这件商品，你可以再说一下商品名。"
            return _cart_unchanged(message)
        totals = _cart_totals(next_groups)
        names = "、".join(f"「{name}」" for name in removed_names)
        message = assistant_message or _remove_items_message(action, names, totals)
        return _hospital_bag_cart_update_envelope(
            action,
            next_groups,
            totals,
            message,
            removed_item_ids=item_ids,
            removed_item_names=removed_names,
        )

    if action == "restore_items":
        if not item_ids:
            return _cart_needs_clarification(assistant_message or "你想加回哪一件？直接告诉我商品名就行。")
        next_groups, restored_names = _restore_hospital_bag_cart_items(
            current_groups,
            item_ids,
            pump_products=available_pump_products,
        )
        totals = _cart_totals(next_groups)
        if not restored_names:
            message = assistant_message or "这些商品已经在购物车里了，不需要重复添加。"
        else:
            names = "、".join(f"「{name}」" for name in restored_names)
            message = assistant_message or f"好，我把{names}加回购物车了，现在预计合计 {_cart_totals_label(totals)}。"
        return _hospital_bag_cart_update_envelope(
            action,
            next_groups,
            totals,
            message,
            restored_item_ids=item_ids,
            restored_item_names=restored_names,
        )

    if action == "replace_items":
        next_groups, replaced_items = _replace_hospital_bag_cart_items(current_groups, item_ids)
        totals = _cart_totals(next_groups)
        if not replaced_items:
            message = assistant_message or "当前购物车里暂时没有可替换成基础款的商品。"
        else:
            names = "、".join(f"「{item['from_name']}」换成「{item['to_name']}」" for item in replaced_items)
            message = assistant_message or f"可以，我先帮你把{names}，现在预计合计 {_cart_totals_label(totals)}。"
        return _hospital_bag_cart_update_envelope(action, next_groups, totals, message, replaced_items=replaced_items)

    if action == "update_quantity":
        quantity_updates = args.get("quantity_updates")
        if not isinstance(quantity_updates, list) or not quantity_updates:
            return _cart_needs_clarification(assistant_message or "你想把哪件商品改成几件？直接告诉我商品名和数量就行。")
        next_groups, updated_names, removed_names = _update_hospital_bag_cart_quantities(current_groups, quantity_updates)
        totals = _cart_totals(next_groups)
        if not updated_names and not removed_names:
            message = assistant_message or "我没有在当前购物车里找到要调整的商品，你可以再说一下商品名。"
        else:
            parts: list[str] = []
            if updated_names:
                parts.append("调整了" + "、".join(f"「{name}」" for name in updated_names))
            if removed_names:
                parts.append("移除了" + "、".join(f"「{name}」" for name in removed_names))
            message = assistant_message or f"好，我已经{'，'.join(parts)}，现在预计合计 {_cart_totals_label(totals)}。"
        return _hospital_bag_cart_update_envelope(action, next_groups, totals, message, removed_item_names=removed_names)

    if action in {"reset_cart", ""}:
        next_groups = _clone_hospital_bag_cart_groups(DEFAULT_HOSPITAL_BAG_CART_GROUPS)
        totals = _cart_totals(next_groups)
        message = assistant_message or f"已经帮你把待产包购物车恢复到默认清单了，现在预计合计 {_cart_totals_label(totals)}。"
        return _hospital_bag_cart_update_envelope("reset_cart", next_groups, totals, message)

    if action == "clarify":
        return _cart_needs_clarification(assistant_message or "你想怎么调整购物车？比如删掉某件、换便宜一点，或者恢复默认清单。")

    return _cart_needs_clarification(assistant_message or "你想怎么调整待产包购物车？")


def _hospital_bag_card_json(form_data: dict[str, Any], *, generation_mode: str = "standard") -> dict[str, Any]:
    return build_hospital_bag_card_json(form_data, generation_mode=generation_mode)


def _needs_context_result(tool_name: str, status: str, summary: str, missing_fields: list[str], question: str) -> dict[str, Any]:
    return {"tool_name": tool_name, "status": status, "summary": summary, "missing_fields": missing_fields, "data": {"confirmation_question": question}}


def _fields_with_defaults(fields: list[dict[str, Any]], default_values: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for field in fields:
        next_field = deepcopy(field)
        value = default_values.get(str(field.get("id") or ""))
        if _has_value(value):
            next_field["default_value"] = value
        result.append(next_field)
    return result


def _allowed_defaults(fields: list[dict[str, Any]], default_values: dict[str, Any]) -> dict[str, Any]:
    field_ids = {str(field.get("id") or "") for field in fields}
    return {key: value for key, value in default_values.items() if key in field_ids and _has_value(value)}


def _confirmed_form_data(args: dict[str, Any]) -> dict[str, Any]:
    for key in ("confirmed_form_data", "form_data", "payload"):
        value = _dict(args.get(key))
        if value:
            return value
    return {}


def _todo_item(item_id: str, title: str, reason: str) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "title": title,
        "status": "pending",
        "plan_reason": reason,
        "priority_type": "essential",
        "priority_label": "必要事项",
    }


def _sanitize_cart_groups(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    groups: list[dict[str, Any]] = []
    for group in value:
        source = _dict(group)
        if not source:
            continue
        items = [_cart_item(item) for item in source.get("items", []) if _dict(item)]
        tone = _text(source.get("tone")) or "rose"
        groups.append({"title": _text(source.get("title")) or "待产包", "tone": tone if tone in {"rose", "mint", "sky"} else "rose", "items": items})
    return groups


def _cart_item(value: Any) -> dict[str, Any]:
    source = _dict(value)
    item = {
        "id": _text(source.get("id")) or _text(source.get("name")) or "item",
        "name": _text(source.get("name")) or _text(source.get("label")) or "待产包用品",
        "desc": _text(source.get("desc")),
        "qty": max(1, int(_number(source.get("qty") or source.get("quantity"), default=1))),
        "price": round(_number(source.get("price"), default=0), 2),
        "keywords": _string_list(source.get("keywords")),
    }
    for key in (
        "currency",
        "price_label",
        "sale_price_label",
        "official_price_usd",
        "sale_price_usd",
        "exchange_rate_usd_cny",
        "product_url",
        "image_url",
        "image_alt",
        "sku_id",
        "model",
    ):
        if source.get(key) is not None:
            item[key] = source[key]
    _apply_default_cart_item_image(item)
    return item


def _cart_totals(groups: list[dict[str, Any]]) -> dict[str, Any]:
    item_count = 0
    subtotal = 0.0
    converted_usd_subtotal = 0.0
    for group in groups:
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue
            qty = max(1, int(_number(item.get("qty"), default=1)))
            item_count += qty
            line_total = _cart_item_line_total_cny(item, qty)
            subtotal = round(subtotal + line_total, 2)
            if _cart_item_currency(item) == "USD":
                converted_usd_subtotal = round(converted_usd_subtotal + line_total, 2)
    subtotal = round(subtotal, 2)
    discount = round(subtotal * 0.08, 2) if item_count > 0 else 0
    shipping = 0
    total = round(subtotal - discount + shipping, 2)
    return {
        "subtotal": subtotal,
        "item_count": item_count,
        "itemCount": item_count,
        "discount": discount,
        "shipping": shipping,
        "total": total,
        "currency": "CNY",
        "currency_totals": [
            {
                "currency": "CNY",
                "subtotal": subtotal,
                "discount": discount,
                "shipping": shipping,
                "total": total,
                "itemCount": item_count,
            }
        ],
        "mixed_currency": False,
        "exchange_rate_usd_cny": HOSPITAL_BAG_CART_USD_TO_CNY_RATE,
        "converted_usd_subtotal": converted_usd_subtotal,
    }


def _cart_groups_from_args(args: dict[str, Any]) -> list[dict[str, Any]]:
    for value in (args.get("groups"), _dict(args.get("cart_update")).get("groups"), _dict(args.get("hospital_bag_cart")).get("groups")):
        groups = _sanitize_cart_groups(value)
        if groups:
            return groups
    return _clone_hospital_bag_cart_groups(DEFAULT_HOSPITAL_BAG_CART_GROUPS)


def _clone_hospital_bag_cart_groups(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "title": _text(group.get("title")),
            "tone": _text(group.get("tone")) or "rose",
            "items": [_clone_hospital_bag_cart_item(item) for item in group.get("items", []) if isinstance(item, dict)],
        }
        for group in groups
        if isinstance(group, dict)
    ]


def _clone_hospital_bag_cart_item(item: dict[str, Any]) -> dict[str, Any]:
    cloned = _cart_item(item)
    _apply_default_cart_item_image(cloned)
    return cloned


def _apply_default_cart_item_image(item: dict[str, Any]) -> None:
    if item.get("image_url"):
        return
    item_id = _text(item.get("id"))
    image_url = HOSPITAL_BAG_CART_PRODUCT_IMAGE_URLS.get(item_id)
    if not image_url:
        return
    item["image_url"] = image_url
    if not item.get("image_alt"):
        item["image_alt"] = _text(item.get("name")) or "商品图"


def _cart_item_line_total_cny(item: dict[str, Any], qty: int) -> float:
    price = _number(item.get("price"), default=0)
    if _cart_item_currency(item) == "USD":
        price = _usd_to_cny(price)
    return round(price * qty, 2)


def _cart_item_currency(item: dict[str, Any]) -> str:
    currency = _text(item.get("currency")).upper() or "CNY"
    return currency if currency in {"USD", "CNY"} else "CNY"


def _hospital_bag_cart_update_envelope(
    action: str,
    groups: list[dict[str, Any]],
    totals: dict[str, Any],
    message: str,
    *,
    before_totals: dict[str, Any] | None = None,
    removed_item_ids: list[str] | None = None,
    removed_item_names: list[str] | None = None,
    restored_item_ids: list[str] | None = None,
    restored_item_names: list[str] | None = None,
    replaced_items: list[dict[str, Any]] | None = None,
    target_budget: float | None = None,
    budget_met: bool | None = None,
) -> dict[str, Any]:
    cart_update: dict[str, Any] = {
        "action": action,
        "groups": groups,
        "totals": totals,
        "removed_item_ids": removed_item_ids or [],
        "removed_item_names": removed_item_names or [],
        "restored_item_ids": restored_item_ids or [],
        "restored_item_names": restored_item_names or [],
        "replaced_items": replaced_items or [],
        "message": message,
    }
    if before_totals is not None:
        cart_update["before_totals"] = before_totals
    if target_budget is not None:
        cart_update["target_budget"] = target_budget
    if budget_met is not None:
        cart_update["budget_met"] = budget_met
    return {"tool_name": "hospital_bag_cart_mutate", "status": "cart_updated", "summary": message, "cart_update": cart_update}


def _cart_needs_clarification(message: str) -> dict[str, Any]:
    return {
        "tool_name": "hospital_bag_cart_mutate",
        "status": "needs_clarification",
        "summary": message,
        "cart_update": {"action": "clarify", "message": message},
    }


def _cart_unchanged(message: str) -> dict[str, Any]:
    return {
        "tool_name": "hospital_bag_cart_mutate",
        "status": "cart_unchanged",
        "summary": message,
        "cart_update": {"action": "clarify", "message": message},
    }


def _hospital_bag_pump_item_ids(pump_products: list[dict[str, Any]]) -> set[str]:
    return {HOSPITAL_BAG_CART_PUMP_ITEM_ID, *(str(product["sku_id"]) for product in pump_products)}


def _hospital_bag_cart_pump_item(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": product["sku_id"],
        "sku_id": product["sku_id"],
        "model": product["model"],
        "name": product["name"],
        "desc": _pump_cart_description(product),
        "qty": 1,
        "price": _usd_to_cny(product["price_usd"]),
        "currency": "CNY",
        "price_label": _pump_price_cny_label(product),
        "sale_price_label": _pump_sale_price_cny_label(product),
        "official_price_usd": float(product["price_usd"]),
        "sale_price_usd": float(product.get("sale_price_usd") or product["price_usd"]),
        "exchange_rate_usd_cny": HOSPITAL_BAG_CART_USD_TO_CNY_RATE,
        "product_url": product["source_url"],
        "image_url": product["image_url"],
        "image_alt": product["name"],
        "keywords": ["吸奶器", "Momcozy", str(product["model"]), str(product["sku_id"])],
    }


def _pump_cart_description(product: dict[str, Any]) -> str:
    features = "、".join(str(feature) for feature in product.get("features", [])[:2])
    if features:
        return f"{features}；{product.get('best_for') or ''}".strip("；")
    return str(product.get("best_for") or "")


def _upsert_hospital_bag_pump_model(
    groups: list[dict[str, Any]],
    product: dict[str, Any],
    *,
    pump_products: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    next_groups = _clone_hospital_bag_cart_groups(groups)
    pump_ids = _hospital_bag_pump_item_ids(pump_products)
    next_item = _hospital_bag_cart_pump_item(product)
    for group in next_groups:
        items = group.get("items", [])
        if not isinstance(items, list):
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            item_id = _text(item.get("id"))
            if item_id == next_item["id"]:
                return next_groups, {"mode": "unchanged", "item_id": next_item["id"], "name": next_item["name"]}
            if item_id in pump_ids or "吸奶器" in _text(item.get("name")):
                from_name = _first_text(item.get("name"))
                items[index] = next_item
                return next_groups, {
                    "mode": "replaced",
                    "from_item_id": item_id,
                    "from_name": from_name,
                    "to_item_id": next_item["id"],
                    "to_name": next_item["name"],
                }
    group = _find_or_create_hospital_bag_cart_group(next_groups, "母乳喂养", "sky")
    group["items"].append(next_item)
    return next_groups, {"mode": "added", "item_id": next_item["id"], "name": next_item["name"]}


def _pump_price_cny_label(product: dict[str, Any]) -> str:
    return _money_label(_usd_to_cny(product["price_usd"]), "CNY")


def _pump_sale_price_cny_label(product: dict[str, Any]) -> str:
    sale_price = product.get("sale_price_usd")
    if not isinstance(sale_price, (int, float)):
        return ""
    prefix = "约 " if product.get("supports_single_unit") else ""
    return f"{prefix}{_money_label(_usd_to_cny(sale_price), 'CNY')}"


def _usd_to_cny(value: Any) -> float:
    return round(_number(value, default=0) * HOSPITAL_BAG_CART_USD_TO_CNY_RATE, 2)


def _cart_totals_label(totals: dict[str, Any]) -> str:
    return _money_label(totals.get("total"), "CNY")


def _money_label(value: Any, currency: str) -> str:
    amount = _number(value, default=0)
    if currency.upper() == "USD":
        return f"${amount:.2f}"
    return f"¥{amount:.2f}"


def _optimize_hospital_bag_cart_budget(
    groups: list[dict[str, Any]],
    *,
    target_budget: float | None,
    budget_mode: str,
    preference: str,
    preserve_item_ids: list[str],
    allow_remove_pump: bool,
) -> dict[str, Any]:
    before_totals = _cart_totals(groups)
    next_groups = _clone_hospital_bag_cart_groups(groups)
    protected_ids = set(HOSPITAL_BAG_CART_PROTECTED_ITEM_IDS)
    protected_ids.update(preserve_item_ids)
    if not allow_remove_pump:
        protected_ids.update(_pump_item_ids_in_groups(groups))

    replaced_items: list[dict[str, Any]] = []
    if preference != "comfort":
        next_groups, replaced_items = _replace_hospital_bag_cart_items(next_groups, ["baby-blanket"])

    removed_ids: list[str] = []
    removed_names: list[str] = []
    if target_budget is None:
        candidate_ids = HOSPITAL_BAG_CART_LIGHT_BUDGET_REMOVE_IDS if budget_mode != "minimal" else set(HOSPITAL_BAG_CART_BUDGET_REMOVE_ORDER)
        next_groups, removed_names = _remove_hospital_bag_cart_items(
            next_groups,
            [item_id for item_id in HOSPITAL_BAG_CART_BUDGET_REMOVE_ORDER if item_id in candidate_ids and item_id not in protected_ids],
        )
        removed_ids = _ids_for_names(groups, removed_names)
    else:
        for item_id in _budget_removal_order(
            allow_remove_pump=allow_remove_pump,
            preference=preference,
            groups=next_groups,
        ):
            if item_id in protected_ids:
                continue
            totals = _cart_totals(next_groups)
            if totals["total"] <= target_budget:
                break
            next_groups_candidate, names = _remove_hospital_bag_cart_items(next_groups, [item_id])
            if not names:
                continue
            next_groups = next_groups_candidate
            removed_ids.append(item_id)
            removed_names.extend(names)

    totals = _cart_totals(next_groups)
    return {
        "groups": next_groups,
        "before_totals": before_totals,
        "totals": totals,
        "target_budget": target_budget,
        "budget_met": target_budget is None or totals["total"] <= target_budget,
        "removed_item_ids": removed_ids,
        "removed_item_names": removed_names,
        "replaced_items": replaced_items,
    }


def _budget_removal_order(
    *,
    allow_remove_pump: bool,
    preference: str,
    groups: list[dict[str, Any]],
) -> list[str]:
    order = list(HOSPITAL_BAG_CART_BUDGET_REMOVE_ORDER)
    if preference == "breastfeeding":
        order = [item_id for item_id in order if item_id not in {"milk-pad", "milk-cream", "milk-storage", "milk-bottle"}] + [
            item_id for item_id in order if item_id in {"milk-pad", "milk-cream", "milk-storage", "milk-bottle"}
        ]
    if allow_remove_pump:
        order.extend(sorted(_pump_item_ids_in_groups(groups)))
    return order


def _pump_item_ids_in_groups(groups: list[dict[str, Any]]) -> set[str]:
    return {
        _text(item.get("id"))
        for group in groups
        for item in group.get("items", [])
        if isinstance(item, dict) and (_text(item.get("id")) == HOSPITAL_BAG_CART_PUMP_ITEM_ID or "吸奶器" in _text(item.get("name")))
    }


def _hospital_bag_budget_message(budget_result: dict[str, Any]) -> str:
    totals = budget_result["totals"]
    target_budget = budget_result.get("target_budget")
    budget_met = bool(budget_result.get("budget_met"))
    removed_names = budget_result.get("removed_item_names") or []
    replaced_items = budget_result.get("replaced_items") or []
    changed_parts: list[str] = []
    if replaced_items:
        changed_parts.append("包被换成基础款")
    if removed_names:
        changed_parts.append("先拿掉" + "、".join(f"「{name}」" for name in removed_names))

    if target_budget is not None and budget_met:
        prefix = f"好，我按 {target_budget:.0f} 元以内帮你压了一版。"
    elif target_budget is not None:
        prefix = f"我先尽量按 {target_budget:.0f} 元以内帮你压了一版，但为了保留吸奶器和基础必需品，目前还会超一点。"
    else:
        prefix = "好，我先帮你切到更省钱的一版。"

    if changed_parts:
        return f"{prefix}{'，'.join(changed_parts)}；吸奶器我先保留。现在预计合计约 {_cart_totals_label(totals)}，共 {totals['itemCount']} 件。"
    return f"{prefix}当前购物车已经比较接近这个要求，吸奶器我先保留。现在预计合计约 {_cart_totals_label(totals)}，共 {totals['itemCount']} 件。"


def _target_budget(value: Any) -> float | None:
    budget = _number(value, default=0)
    if budget <= 0:
        return None
    return round(budget, 2)


def _missing_item_message(action: str) -> str:
    if action == "mark_provided":
        return "医院会提供哪些？你直接告诉我物品名，我帮你从购物车里拿掉。"
    if action == "mark_owned":
        return "家里已经有哪些？你直接告诉我物品名，我帮你从购物车里拿掉。"
    return "你想删哪一件？直接告诉我商品名就行。"


def _remove_items_message(action: str, names: str, totals: dict[str, Any]) -> str:
    if action == "mark_provided":
        return f"好，医院会提供的{names}我先从购物车里拿掉了，现在预计合计 {_cart_totals_label(totals)}。"
    if action == "mark_owned":
        return f"好，家里已经有的{names}我先从购物车里拿掉了，现在预计合计 {_cart_totals_label(totals)}。"
    return f"已帮你从购物车里删掉{names}，现在预计合计 {_cart_totals_label(totals)}。"


def _replace_hospital_bag_cart_items(groups: list[dict[str, Any]], item_ids: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    requested = set(item_ids) if item_ids else set(HOSPITAL_BAG_CART_BUDGET_REPLACEMENTS)
    replaced_items: list[dict[str, Any]] = []
    next_groups: list[dict[str, Any]] = []
    for group in groups:
        items: list[dict[str, Any]] = []
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue
            item_id = _text(item.get("id"))
            replacement = HOSPITAL_BAG_CART_BUDGET_REPLACEMENTS.get(item_id)
            if item_id in requested and replacement:
                items.append(_clone_hospital_bag_cart_item(replacement))
                replaced_items.append(
                    {
                        "from_item_id": item_id,
                        "from_name": _first_text(item.get("name")),
                        "to_item_id": replacement["id"],
                        "to_name": replacement["name"],
                    }
                )
                continue
            items.append(dict(item))
        next_groups.append({**group, "items": items})
    return next_groups, replaced_items


def _remove_hospital_bag_cart_items(groups: list[dict[str, Any]], item_ids: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    requested = set(item_ids)
    removed_names: list[str] = []
    next_groups: list[dict[str, Any]] = []
    for group in groups:
        items: list[dict[str, Any]] = []
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue
            if _text(item.get("id")) in requested:
                name = _first_text(item.get("name"))
                if name:
                    removed_names.append(name)
                continue
            items.append(dict(item))
        next_groups.append({**group, "items": items})
    return next_groups, removed_names


def _restore_hospital_bag_cart_items(
    groups: list[dict[str, Any]],
    item_ids: list[str],
    *,
    pump_products: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    next_groups = _clone_hospital_bag_cart_groups(groups)
    existing_ids = {_text(item.get("id")) for group in next_groups for item in group.get("items", []) if isinstance(item, dict)}
    restored_names: list[str] = []
    for item_id in item_ids:
        if item_id in existing_ids:
            continue
        original_id = HOSPITAL_BAG_CART_REPLACEMENT_ORIGINAL_BY_ID.get(item_id, item_id)
        default = _default_hospital_bag_cart_item(original_id, pump_products=pump_products)
        if default is None:
            continue
        group_title, group_tone, item = default
        group = _find_or_create_hospital_bag_cart_group(next_groups, group_title, group_tone)
        group["items"].append(item)
        existing_ids.add(item["id"])
        restored_names.append(item["name"])
    return next_groups, restored_names


def _update_hospital_bag_cart_quantities(
    groups: list[dict[str, Any]],
    quantity_updates: list[Any],
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    update_by_id: dict[str, int] = {}
    for update in quantity_updates:
        if not isinstance(update, dict):
            continue
        item_id = _first_text(update.get("item_id"))
        if item_id:
            update_by_id[item_id] = max(0, int(_number(update.get("qty"), default=1)))

    updated_names: list[str] = []
    removed_names: list[str] = []
    next_groups: list[dict[str, Any]] = []
    for group in groups:
        items: list[dict[str, Any]] = []
        for item in group.get("items", []):
            if not isinstance(item, dict):
                continue
            item_id = _text(item.get("id"))
            if item_id not in update_by_id:
                items.append(dict(item))
                continue
            qty = update_by_id[item_id]
            name = _first_text(item.get("name"))
            if qty <= 0:
                if name:
                    removed_names.append(name)
                continue
            next_item = dict(item)
            next_item["qty"] = qty
            items.append(next_item)
            if name:
                updated_names.append(name)
        next_groups.append({**group, "items": items})
    return next_groups, updated_names, removed_names


def _default_hospital_bag_cart_item(
    item_id: str,
    *,
    pump_products: list[dict[str, Any]],
) -> tuple[str, str, dict[str, Any]] | None:
    product = find_pump_product(pump_products, item_id)
    if product:
        return "母乳喂养", "sky", _hospital_bag_cart_pump_item(product)
    for group in DEFAULT_HOSPITAL_BAG_CART_GROUPS:
        for item in group.get("items", []):
            if isinstance(item, dict) and _text(item.get("id")) == item_id:
                return _text(group.get("title")), _text(group.get("tone")) or "rose", _clone_hospital_bag_cart_item(item)
    return None


def _find_or_create_hospital_bag_cart_group(groups: list[dict[str, Any]], title: str, tone: str) -> dict[str, Any]:
    for group in groups:
        if _text(group.get("title")) == title:
            return group
    group = {"title": title, "tone": tone, "items": []}
    groups.append(group)
    return group


def _ids_for_names(groups: list[dict[str, Any]], names: list[str]) -> list[str]:
    wanted = set(names)
    ids: list[str] = []
    for group in groups:
        for item in group.get("items", []):
            if isinstance(item, dict) and _first_text(item.get("name")) in wanted:
                ids.append(_text(item.get("id")))
    return [item_id for item_id in ids if item_id]


def _item_match_key(value: Any) -> str:
    return "".join(ch.lower() for ch in _text(value) if ch.isalnum())


def _number(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = _text(value)
    return [text] if text else []


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, list):
            nested = _first_text(*value)
            if nested:
                return nested
        elif isinstance(value, dict):
            nested = _first_text(*value.values())
            if nested:
                return nested
        else:
            text = _text(value)
            if text:
                return text
    return ""


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _has_value(value: Any) -> bool:
    if isinstance(value, list):
        return any(_has_value(item) for item in value)
    if isinstance(value, dict):
        return any(_has_value(item) for item in value.values())
    return bool(_text(value))
