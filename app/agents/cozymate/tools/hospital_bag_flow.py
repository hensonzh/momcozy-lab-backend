from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any


HOSPITAL_BAG_FORM_ID = "hospital_bag_intake"
HOSPITAL_BAG_WORKFLOW_TYPE = "hospital_bag"
HOSPITAL_BAG_WORKFLOW_SCHEMA_VERSION = "v1"
HOSPITAL_BAG_DISCLAIMER = "请优先遵循医院要求和医生/助产士的具体指导。"
HOSPITAL_BAG_CART_LINK = "[打开待产包购物车](/hospital-bag-cart)"

_REQUIRED_FIELDS = {
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


def ensure_hospital_bag_completion_followup(
    text: str,
    *,
    card: dict[str, Any] | None = None,
) -> str:
    normalized = str(text or "").strip()
    if "/hospital-bag-cart" in normalized:
        return normalized
    followup = build_hospital_bag_followup(card or {})["message"]
    paragraphs = [paragraph.strip() for paragraph in followup.split("\n\n") if paragraph.strip()]
    suffix = [
        paragraph
        for index, paragraph in enumerate(paragraphs)
        if (not normalized or index > 0) and not _hospital_bag_followup_paragraph_present(normalized, paragraph)
    ]
    if not suffix:
        return normalized
    return "\n\n".join([normalized, *suffix]) if normalized else "\n\n".join(suffix)


def _hospital_bag_followup_paragraph_present(text: str, paragraph: str) -> bool:
    if "/hospital-bag-cart" in paragraph:
        return "/hospital-bag-cart" in text
    if paragraph.startswith("特殊物品我按这几个情况做了取舍"):
        return "特殊物品我按这几个情况做了取舍" in text
    if paragraph.startswith("我只保留和孕周、喂养、医院确认真正相关的非常规物品"):
        return "我只保留和孕周、喂养、医院确认真正相关的非常规物品" in text
    if "不用一次买完" in paragraph:
        return "不用一次买完" in text and "购物车" in text
    return paragraph in text


def build_hospital_bag_card_json(
    form_data: dict[str, Any],
    *,
    generation_mode: str = "standard",
    as_of_date: date | None = None,
) -> dict[str, Any]:
    due_date_or_week = _text(form_data.get("due_date_or_week")) or "待确认"
    birth_path = _normalize_birth_path(form_data.get("birth_path")) or "待确认"
    feeding_intention = _normalize_feeding(form_data.get("feeding_intention")) or "待确认"
    first_birth = _normalize_first_birth(form_data.get("first_birth")) or "待确认"
    fetus_count = _normalize_fetus_count(form_data.get("fetus_count")) or "待确认"
    support_person = _normalize_support(form_data.get("support_person")) or "待确认"
    pregnancy_notes = list(
        dict.fromkeys(
            [
                *_text_list(form_data.get("pregnancy_history_or_notes")),
                *_text_list(form_data.get("medical_notes")),
                *_text_list(form_data.get("doctor_notes")),
            ]
        )
    )
    return_to_work = _text(form_data.get("return_to_work_timing")) or "待确认"
    top_worries = _text_list(form_data.get("top_worries"))
    provided_items = _provided_items(form_data.get("hospital_provided_items"))
    stage = _hospital_bag_stage(
        due_date_or_week,
        generation_mode=generation_mode,
        as_of_date=as_of_date,
    )
    context: dict[str, Any] = {
        "stage": stage,
        "birth_path": birth_path,
        "feeding_intention": feeding_intention,
        "first_birth": first_birth,
        "fetus_count": fetus_count,
        "pregnancy_history_or_notes": pregnancy_notes,
        "return_to_work_timing": return_to_work,
        "top_worries": top_worries,
        "support_person": support_person,
        "provided_items": provided_items,
    }
    packing_groups = _packing_groups(context)
    personalized_notes = _personalized_notes(context)
    if provided_items:
        personalized_notes.append(f"已从清单移除医院提供的物品：{'、'.join(provided_items)}。")
    return {
        "card_type": "hospital_bag_card",
        "schema_version": "1.0",
        "title": "待产包",
        "subtitle": "个性化入院物品清单",
        "generation_mode": stage,
        "owner": {
            "due_date_or_week": due_date_or_week,
            "birth_setting": _text(form_data.get("birth_setting")) or "待确认",
            "birth_path": birth_path,
            "first_birth": first_birth,
            "feeding_intention": feeding_intention,
            "support_person": support_person,
            "fetus_count": fetus_count,
            "return_to_work_timing": return_to_work,
        },
        "hospital_context": {
            "expected_stay": _text(form_data.get("expected_stay")) or "待确认",
            "hospital_provided_items": provided_items,
            "items_to_confirm_with_hospital": _confirmation_questions(context)[:5],
        },
        "focus_items": _focus_items(context),
        "hospital_questions": _confirmation_questions(context)[:8],
        "packing_groups": packing_groups,
        "missing_or_to_buy": [],
        "timeline": _timeline(stage),
        "personalized_notes": personalized_notes,
        "missing_fields": [label for key, label in _REQUIRED_FIELDS.items() if not _has_value(form_data.get(key))],
        "disclaimer": HOSPITAL_BAG_DISCLAIMER,
    }


def _packing_groups(context: dict[str, Any]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = [
        {"group_id": "documents", "title": "证件文件包", "items": _document_items(context)},
        {"group_id": "mom_hospital_bag", "title": "妈妈住院包", "items": _mom_items(context)},
        {"group_id": "baby_discharge_bag", "title": "宝宝出院包", "items": _baby_items(context)},
        {"group_id": "support_person_bag", "title": "陪产人包", "items": _support_items(context)},
        {"group_id": "car_backup_bag", "title": "车上备用包", "items": _car_items(context)},
    ]
    postpartum_items = _postpartum_items(context)
    if postpartum_items:
        groups.append({"group_id": "postpartum_home_first_week", "title": "产后回家第一周用品", "items": postpartum_items})
    provided_items = _text_list(context.get("provided_items"))
    filtered = [
        {**group, "items": [item for item in group["items"] if not _hospital_provides(item["label"], provided_items)]}
        for group in groups
        if group["items"]
    ]
    for group in filtered:
        for item in group["items"]:
            explanation = _item_explanation(str(item["label"]))
            if explanation:
                item.setdefault("explain", explanation)
    return filtered


def _document_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    items = [
        _item("身份证件", "must", copy_requirement="原件"),
        _item("医保卡/保险卡", "must", copy_requirement="原件"),
        _item("产检本/产检资料", "must", copy_requirement="原件"),
        _item("检查报告/化验单", "must" if _meaningful_pregnancy_notes(context) else "recommended", copy_requirement="按医院要求"),
        _item("医院预登记信息", "confirm_first", confirm_question="确认是否已完成医院预登记，以及入院当天需要出示什么。"),
        _item("银行卡/手机支付", "must"),
        _item("紧急联系人信息", "recommended"),
        _item("医生/医院联系电话", "recommended"),
        _item("分娩沟通单", "recommended" if context["first_birth"] == "是" else "nice_to_have"),
        _item("准生证/户口本", "confirm_first", confirm_question="确认医院是否要求携带准生证、户口本及复印件。"),
    ]
    if context["first_birth"] == "否":
        items.append(
            _personalize(_item("大宝照护安排", "recommended", note="分别确认入院、住院和出院当天的负责人。"), "first_birth", "否", "新增")
        )
    return items


def _mom_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    stage = str(context["stage"])
    formula_only = context["feeding_intention"] == "配方"
    items = [
        _item("手机充电线和充电器", "must", note="长充电线更适合病床旁使用。"),
        _item("宽松出院衣物", "must", quantity="1套"),
        _item("开襟睡衣" if formula_only else "开襟睡衣/哺乳睡衣", "recommended", quantity="1-2套"),
        _item("舒适内衣" if formula_only else "哺乳文胸/舒适内衣", "recommended", quantity="2-3件"),
        _item("防滑拖鞋", "must", quantity="1双"),
        _item("吸管杯", "must", quantity="1个"),
        _personalize(
            _item("产褥垫/产妇卫生巾", "must" if stage in {"packing", "immediate"} else "recommended", quantity=_pad_quantity(context)),
            "due_date_or_week",
            _stage_label(stage),
            "提权" if stage in {"packing", "immediate"} else "暂缓囤货",
        ),
        _item("一次性内裤", "must" if stage in {"packing", "immediate"} else "recommended", quantity="若干条"),
        _item("洗漱用品", "recommended", quantity="旅行装"),
        _item("纸巾/湿巾", "recommended", quantity="少量"),
        _item("毛巾", "recommended", quantity="1-2条"),
        _item("外套/披肩", "recommended", quantity="1件"),
        _item("胎监带", "confirm_first", confirm_question="确认医院是否要求自带胎监带，以及需要几条。"),
    ]
    if context["birth_path"] == "剖宫产" or _has_choice(context, "top_worries", "怕剖宫产恢复"):
        condition = "剖宫产" if context["birth_path"] == "剖宫产" else "怕剖宫产恢复"
        items[2:2] = [
            _personalize(
                _item("高腰宽松内裤", "recommended", quantity="若干条", note="更不容易压到腹部。"), "birth_path", condition, "新增"
            ),
            _personalize(_item("不压腹出院裤/裙", "recommended", quantity="1套"), "birth_path", condition, "新增"),
        ]
        items.append(
            _personalize(
                _item("收腹带", "confirm_first", confirm_question="剖宫产先确认医生或医院是否建议使用收腹带。"),
                "birth_path",
                condition,
                "新增",
            )
        )
    if _has_choice(context, "pregnancy_history_or_notes", "妊娠糖尿病"):
        items.extend(
            [
                _personalize(
                    _item("血糖记录/饮食医嘱", "must", note="只按医生已经给出的方案准备。"),
                    "pregnancy_history_or_notes",
                    "妊娠糖尿病",
                    "新增",
                ),
                _personalize(
                    _item("医生允许的加餐", "confirm_first", confirm_question="确认产房和病区允许携带的食物类型。"),
                    "pregnancy_history_or_notes",
                    "妊娠糖尿病",
                    "新增",
                ),
            ]
        )
    if _has_choice(context, "pregnancy_history_or_notes", "血压", "子痫"):
        items.append(
            _personalize(
                _item("血压记录/用药清单", "must", note="只记录医生已确认的信息，不自行调整用药。"),
                "pregnancy_history_or_notes",
                "血压或子痫前期风险",
                "新增",
            )
        )
    if _has_choice(context, "pregnancy_history_or_notes", "胎盘"):
        items.append(_personalize(_item("近期B超/医生医嘱", "must"), "pregnancy_history_or_notes", "胎盘问题", "提权"))
    if _limited_support(context):
        items.append(_personalize(_item("床边收纳袋", "recommended"), "support_person", str(context["support_person"]), "提权"))
    return items


def _baby_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    fetus_count = str(context["fetus_count"])
    source = ("fetus_count", fetus_count, "数量调整") if fetus_count in {"双胎", "三胎及以上"} else None
    items = [
        _item("宝宝出院衣物", "must", quantity=_baby_quantity(context, "1套")),
        _item("备用连体衣", "recommended", quantity=_baby_quantity(context, "1-2套")),
        _item("包被", "must", quantity=_baby_quantity(context, "1条")),
        _item("小毯子", "nice_to_have", quantity=_baby_quantity(context, "1条")),
        _item("纸尿裤", "confirm_first", confirm_question="确认医院是否提供纸尿裤；如果不提供，再问建议数量。"),
        _item("湿巾/棉柔巾", "recommended", quantity=_baby_quantity(context, "1-2包")),
        _item("帽子/袜子", "recommended", quantity=_baby_quantity(context, "各1-2件")),
        _item("口水巾/小方巾", "nice_to_have", quantity="2-3条"),
        _item("奶瓶", "confirm_first", confirm_question="确认医院是否允许或需要自带奶瓶。"),
        _item("安全提篮/安全座椅", "confirm_first", confirm_question="确认出院交通是否需要安全提篮或安全座椅。"),
    ]
    if source:
        items = [_personalize(item, *source) for item in items]
    if _has_choice(context, "pregnancy_history_or_notes", "NICU"):
        items.append(
            _personalize(
                _item("NICU探视/送奶规则确认", "confirm_first", confirm_question="确认宝宝如需 NICU 时，探视、送奶和标签要求。"),
                "pregnancy_history_or_notes",
                "宝宝可能 NICU",
                "新增",
            )
        )
    if _has_choice(context, "pregnancy_history_or_notes", "早产"):
        items.append(
            _personalize(
                _item("小码/早产儿衣物确认", "confirm_first", confirm_question="先问医院是否需要自备特殊尺码衣物。"),
                "pregnancy_history_or_notes",
                "早产风险",
                "新增",
            )
        )
    return items


def _support_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    if _limited_support(context):
        return [
            _item("远程联系人名单", "must"),
            _item("去医院交通方案", "must"),
            _item("出院接送安排", "recommended"),
            _item("家中照护安排", "recommended"),
            _item("紧急备用联系人", "recommended"),
        ]
    return [
        _item("陪产人身份证件", "must", copy_requirement="原件"),
        _item("手机充电器", "must"),
        _item("充电宝", "recommended"),
        _item("换洗衣物", "recommended", quantity="1套"),
        _item("外套", "recommended", quantity="1件"),
        _item("洗漱用品", "recommended", quantity="1套"),
        _item("水和零食", "recommended", quantity="按住院天数"),
        _item("停车/支付用品", "recommended"),
    ]


def _car_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _item("备用产褥垫/卫生巾", "recommended", quantity="少量"),
        _item("纸巾/湿巾", "recommended", quantity="少量"),
        _item("水", "recommended", quantity="少量"),
        _item("备用衣物", "nice_to_have", quantity="1套"),
        _item("医院路线和停车信息", "recommended"),
        _item("塑料袋/收纳袋", "recommended"),
        _item("车内充电线", "recommended"),
        _item("夜间入口信息", "confirm_first", confirm_question="确认夜间急诊或产科入口在哪里。"),
    ]


def _postpartum_items(context: dict[str, Any]) -> list[dict[str, Any]]:
    feeding = str(context["feeding_intention"])
    if feeding == "配方":
        return [
            _personalize(
                _item("奶瓶", "confirm_first", confirm_question="确认医院是否允许自带奶瓶，或是否由医院提供。"),
                "feeding_intention",
                feeding,
                "新增",
            ),
            _personalize(
                _item("配方奶", "confirm_first", confirm_question="确认医院是否允许自带配方奶，以及品牌或规格要求。"),
                "feeding_intention",
                feeding,
                "新增",
            ),
            _personalize(_item("奶瓶清洁用品", "recommended", quantity="少量"), "feeding_intention", feeding, "新增"),
            _item("奶嘴", "recommended", quantity="少量"),
            _item("喂养记录工具", "recommended"),
        ]
    if feeding not in {"母乳", "混合", "纯泵奶", "待确认", "未确定"}:
        return []
    high_priority_pump = (
        _early_return_to_work(context)
        or _has_choice(context, "top_worries", "怕母乳不够")
        or _has_choice(context, "pregnancy_history_or_notes", "NICU")
    )
    pump_sources = [("feeding_intention", feeding, "新增")]
    if _early_return_to_work(context):
        pump_sources.append(("return_to_work_timing", str(context["return_to_work_timing"]), "提权"))
    if _has_choice(context, "top_worries", "怕母乳不够"):
        pump_sources.append(("top_worries", "怕母乳不够", "提权"))
    items = [
        _personalize(_item("哺乳文胸/哺乳背心", "recommended", quantity="2-3件"), "feeding_intention", feeding, "新增"),
        _personalize(_item("防溢乳垫", "recommended", quantity="5-10片"), "feeding_intention", feeding, "新增"),
        _personalize_many(
            _item(
                "便携式吸奶器",
                "must" if high_priority_pump else "recommended",
                quantity="1台",
                note="母乳或混合喂养时可作为备用，不是必须购买。",
            ),
            *pump_sources,
        ),
        _item("储奶袋/储奶瓶", "must" if _early_return_to_work(context) else "recommended", quantity="少量"),
        _item("乳头霜", "recommended", quantity="1支"),
        _item("乳盾", "confirm_first", confirm_question="是否需要乳盾，建议先听医院或哺乳顾问建议。"),
        _item("哺乳枕", "nice_to_have"),
        _item("喂养记录工具", "recommended"),
    ]
    if _early_return_to_work(context):
        items.extend(
            [
                _personalize(
                    _item("冷藏包/冰袋", "recommended", quantity="1套"),
                    "return_to_work_timing",
                    str(context["return_to_work_timing"]),
                    "提权",
                ),
                _item("标签笔", "recommended", quantity="1支"),
                _item("吸奶配件清洁包", "recommended", quantity="1套"),
            ]
        )
    if feeding == "混合":
        items.extend(
            [
                _item("奶瓶", "confirm_first", confirm_question="确认医院是否允许或需要自带奶瓶。"),
                _item("奶瓶清洁用品", "recommended", quantity="少量"),
            ]
        )
    return items


def _confirmation_questions(context: dict[str, Any]) -> list[str]:
    questions = [
        "准生证/户口本：确认医院是否要求携带原件和复印件。",
        "纸尿裤/宝宝衣物：确认医院是否提供，避免重复携带。",
        "胎监带：确认医院是否要求自带，以及需要几条。",
        "陪产/探视：确认是否允许陪产或探视，以及陪产人是否可以过夜。",
        "水和零食：确认产房和病区是否允许携带。",
    ]
    if context["birth_path"] == "剖宫产":
        questions.append("收腹带/术后用品：剖宫产先问医生或医院是否建议准备。")
    if context["feeding_intention"] == "配方":
        questions.append("奶瓶/配方奶：确认医院是否允许携带，或是否由医院提供。")
    elif context["feeding_intention"] in {"母乳", "混合", "纯泵奶"}:
        questions.append("母乳喂养支持：确认医院是否有产后哺乳指导或泌乳顾问资源。")
    if context["stage"] in {"packing", "immediate"}:
        questions.append("住院时长/出院要求：确认预计住院几天，以及宝宝出院衣物是否有要求。")
    return list(dict.fromkeys(questions))


def _focus_items(context: dict[str, Any]) -> list[str]:
    items = ["身份证件", "医保卡/保险卡", "产检资料", "手机充电线和充电器", "宝宝出院衣物和包被"]
    if context["stage"] in {"packing", "immediate"}:
        items[3:3] = ["产褥垫/产妇卫生巾", "一次性内裤"]
    if context["feeding_intention"] in {"母乳", "混合", "纯泵奶"}:
        items.append("哺乳文胸/防溢乳垫")
    return items[:7]


def _personalized_notes(context: dict[str, Any]) -> list[str]:
    stage_notes = {
        "planning": "现在更适合先把医院要求和大方向定下来，不用急着一次买齐。",
        "purchase": "这个阶段可以开始集中准备基础物品，但医院会提供的东西先别重复买。",
        "packing": "已经接近实际打包阶段，清单优先保留能直接装包的物品。",
        "immediate": "现在最重要的是随手能拿走，先保留证件、妈妈护理和宝宝出院基础物品。",
    }
    notes = [stage_notes[str(context["stage"])]]
    if context["birth_path"] == "剖宫产":
        notes.append("你选择了剖宫产，所以保留了更宽松、方便拿取和术后更友好的物品提醒。")
    if context["feeding_intention"] in {"母乳", "混合", "纯泵奶"}:
        notes.append("你有母乳喂养意向，所以保留哺乳文胸、防溢乳垫和吸奶器备用项。")
    elif context["feeding_intention"] == "配方":
        notes.append("你选择配方喂养，所以只保留配方喂养用品，并把奶瓶和配方奶列为医院确认项。")
    if _has_choice(context, "top_worries", "怕漏买"):
        notes.append("你担心漏买，所以清单保留了各场景的必需项和需要先向医院确认的项目。")
    if _meaningful_pregnancy_notes(context):
        notes.append("你填写的特殊注意事项只用于打包和医院确认提醒，不做医学判断。")
    return notes[:4]


def build_hospital_bag_followup(card: dict[str, Any]) -> dict[str, str]:
    raw_owner = card.get("owner")
    owner: dict[str, Any] = dict(raw_owner) if isinstance(raw_owner, dict) else {}
    labels = {
        str(item.get("label") or "")
        for group in card.get("packing_groups", [])
        if isinstance(group, dict)
        for item in group.get("items", [])
        if isinstance(item, dict)
    }
    lines = ["待产包清单我整理好了。"]
    special: list[str] = []
    if "高腰宽松内裤" in labels:
        special.append("考虑到你倾向剖宫产，我为你准备了高腰宽松内裤和不压腹出院裤/裙；收腹带先放在需要问医生的项目里。")
    feeding = str(owner.get("feeding_intention") or "")
    if feeding == "配方":
        special.append("考虑到你准备配方喂养，奶瓶和配方奶保留为医院确认项，没有加入吸奶器、乳垫等母乳喂养用品。")
    elif feeding in {"母乳", "混合", "纯泵奶"} and "便携式吸奶器" in labels:
        special.append("考虑到你准备母乳或混合喂养，清单保留了哺乳文胸/哺乳背心、防溢乳垫、便携式吸奶器和储奶袋/储奶瓶。")
    if "冷藏包/冰袋" in labels:
        return_timing = str(owner.get("return_to_work_timing") or "较早返工")
        timing_suffix = "" if any(token in return_timing for token in ("返工", "复工", "上班")) else "返工"
        special.append(f"考虑到你预计{return_timing}{timing_suffix}，清单加入了冷藏包/冰袋、标签笔和吸奶配件清洁包。")
    fetus_count = str(owner.get("fetus_count") or "")
    if fetus_count in {"双胎", "三胎及以上"}:
        special.append(f"考虑到这次是{fetus_count}，宝宝出院衣物、包被和消耗品数量已经按宝宝数调整。")
    if special:
        lines.append("特殊物品我按这几个情况做了取舍：\n" + "\n".join(f"- {line}" for line in special))
    else:
        lines.append("我只保留和孕周、喂养、医院确认真正相关的非常规物品；不确定的先放在需要确认的项目里。")
    lines.extend(
        [
            "具体可以看下面的待产包清单。我也把适合放入购物车参考的妈妈/宝宝用品整理好了，不用一次买完，先看优先级，按实际情况删减后再决定是否购买。",
            f"**{HOSPITAL_BAG_CART_LINK}**",
        ]
    )
    return {"kind": "hospital_bag_cart", "message": "\n\n".join(lines)}


def _timeline(stage: str) -> list[str]:
    if stage == "planning":
        return ["下次产检前：先问医院入院材料和提供物品。", "32 周前后：再把基础母婴用品补齐。"]
    if stage == "purchase":
        return ["这两周：先买齐妈妈护理、宝宝出院和证件收纳用品。", "35-36 周：把主包和证件袋实际装好。"]
    if stage == "packing":
        return ["今天或本周：把证件袋、妈妈包和宝宝出院包分开装好。", "出发前：只复核手机、充电线、证件和医院联系信息。"]
    return ["现在：证件、手机、妈妈护理和宝宝出院物品先放固定位置。", "出发前：联系医院或医生，确认入院入口和需要携带的材料。"]


def _hospital_bag_stage(due_text: str, *, generation_mode: str, as_of_date: date | None) -> str:
    if generation_mode == "immediate":
        return "immediate"
    week = _pregnancy_week(due_text, as_of_date=as_of_date)
    if week is None:
        return "purchase"
    if week >= 37:
        return "immediate"
    if week >= 36:
        return "packing"
    if week >= 32:
        return "purchase"
    return "planning"


def _pregnancy_week(value: Any, *, as_of_date: date | None) -> int | None:
    text = _text(value)
    match = re.search(r"(\d{1,2})\s*(?:周|w|week)", text, re.IGNORECASE)
    if match:
        week = int(match.group(1))
        return week if 1 <= week <= 42 else None
    try:
        due_date = date.fromisoformat(text.replace("/", "-"))
    except ValueError:
        return None
    today = as_of_date or datetime.now(timezone.utc).date()
    gestational_days = 280 - (due_date - today).days
    return max(1, min(42, gestational_days // 7)) if gestational_days >= 0 else None


def _item(label: str, priority: str, **details: str) -> dict[str, Any]:
    return {"label": label, "priority": priority, **{key: value for key, value in details.items() if value}}


def _personalize(item: dict[str, Any], field: str, condition: str, effect: str) -> dict[str, Any]:
    return {
        **item,
        "personalized_by": [
            {
                "field": field,
                "field_label": _REQUIRED_FIELDS.get(field, field),
                "condition": condition,
                "effect": effect,
            }
        ],
    }


def _personalize_many(item: dict[str, Any], *sources: tuple[str, str, str]) -> dict[str, Any]:
    return {
        **item,
        "personalized_by": [
            {
                "field": field,
                "field_label": _REQUIRED_FIELDS.get(field, field),
                "condition": condition,
                "effect": effect,
            }
            for field, condition, effect in sources
            if condition
        ],
    }


def _item_explanation(label: str) -> str:
    if "防溢乳垫" in label:
        return "放在内衣里吸收漏奶，避免衣服被打湿。"
    if "胎监带" in label:
        return "做胎心监护时固定探头用，有些医院要求自带。"
    if "收腹带" in label:
        return "产后腹部支撑用品，剖宫产尤其要先问医生。"
    if "分娩沟通单" in label:
        return "记录生产偏好和需要提前沟通的事，入院时方便给医护看。"
    return ""


def _normalize_birth_path(value: Any) -> str:
    text = _text(value).lower()
    if any(token in text for token in ("剖宫", "剖腹", "c-section", "cesarean")):
        return "剖宫产"
    if any(token in text for token in ("顺产", "自然分娩", "vaginal")):
        return "顺产"
    if any(token in text for token in ("不确定", "还没确定", "unknown")):
        return "还不确定"
    return _text(value)


def _normalize_feeding(value: Any) -> str:
    text = _text(value).lower()
    if any(token in text for token in ("混合", "mixed", "combo")):
        return "混合"
    if any(token in text for token in ("配方", "奶粉", "formula")):
        return "配方"
    if any(token in text for token in ("纯泵", "泵奶", "exclusive pumping")):
        return "纯泵奶"
    if any(token in text for token in ("母乳", "亲喂", "breast")):
        return "母乳"
    if any(token in text for token in ("不确定", "还没想好", "unknown")):
        return "未确定"
    return _text(value)


def _normalize_first_birth(value: Any) -> str:
    text = _text(value).lower()
    if text in {"是", "第一胎", "一胎", "yes", "true"}:
        return "是"
    if text in {"否", "不是", "二胎", "no", "false"}:
        return "否"
    return _text(value)


def _normalize_fetus_count(value: Any) -> str:
    text = _text(value).lower()
    if any(token in text for token in ("三胎", "多胎", "triplet")):
        return "三胎及以上"
    if any(token in text for token in ("双胎", "双胞胎", "twin")):
        return "双胎"
    if any(token in text for token in ("单胎", "singleton")):
        return "单胎"
    return _text(value)


def _normalize_support(value: Any) -> str:
    text = _text(value)
    if any(token in text for token in ("白天主要自己", "白天自己")):
        return "白天主要自己"
    if any(token in text for token in ("夜间主要自己", "夜里自己")):
        return "夜间主要自己"
    if any(token in text for token in ("支持少", "没人帮", "一个人", "主要自己")):
        return "支持少"
    if any(token in text for token in ("有人全天帮忙", "全天", "伴侣", "家人", "有人帮")):
        return "有人全天帮忙"
    return text


def _limited_support(context: dict[str, Any]) -> bool:
    return any(token in str(context["support_person"]) for token in ("白天主要自己", "夜间主要自己", "支持少", "没有", "不确定"))


def _early_return_to_work(context: dict[str, Any]) -> bool:
    text = str(context["return_to_work_timing"])
    if any(token in text for token in ("暂不", "不返工", "半年", "6个月", "六个月", "一年")):
        return False
    return bool(re.search(r"(?:[168]|一|六|八)\s*周|(?:[12]|一|两)\s*个?月", text))


def _baby_quantity(context: dict[str, Any], default: str) -> str:
    count = str(context["fetus_count"])
    if count == "双胎":
        return (
            default.replace("1套", "2套")
            .replace("1条", "2条")
            .replace("1-2套", "2-3套")
            .replace("1-2包", "2-3包")
            .replace("各1-2件", "各2份")
        )
    if count == "三胎及以上":
        return (
            default.replace("1套", "按宝宝数各1套")
            .replace("1条", "按宝宝数各1条")
            .replace("1-2套", "按宝宝数各1套+备用")
            .replace("1-2包", "按宝宝数上调")
            .replace("各1-2件", "按宝宝数各1份")
        )
    return default


def _pad_quantity(context: dict[str, Any]) -> str:
    return "20片左右" if context["birth_path"] == "剖宫产" else "10-20片"


def _has_choice(context: dict[str, Any], field: str, *tokens: str) -> bool:
    return any(any(token.lower() in value.lower() for token in tokens) for value in _text_list(context.get(field)))


def _meaningful_pregnancy_notes(context: dict[str, Any]) -> bool:
    return any("没有" not in value and value not in {"无", "待确认"} for value in _text_list(context.get("pregnancy_history_or_notes")))


def _provided_items(value: Any) -> list[str]:
    values = _text_list(value)
    if len(values) == 1:
        values = [part.strip() for part in re.split(r"[、,，;；\n]+", values[0]) if part.strip()]
    return list(dict.fromkeys(values))


def _hospital_provides(label: str, provided_items: list[str]) -> bool:
    normalized_label = label.replace("备用", "")
    return any(item in normalized_label or normalized_label in item for item in provided_items)


def _stage_label(stage: str) -> str:
    return {"planning": "32周前计划版", "purchase": "32-35周采购版", "packing": "36周打包版", "immediate": "37周后临产版"}[stage]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [text for item in value if (text := _text(item))]
    text = _text(value)
    return [text] if text else []


def _has_value(value: Any) -> bool:
    if isinstance(value, list):
        return any(_has_value(item) for item in value)
    return bool(_text(value))
