from app.agents.cozymate.tools.hospital_bag_flow import (
    build_hospital_bag_card_json,
    build_hospital_bag_followup,
    ensure_hospital_bag_completion_followup,
)


def _items(card: dict) -> list[dict]:
    return [item for group in card["packing_groups"] for item in group["items"]]


def _item(card: dict, label: str) -> dict:
    return next(item for item in _items(card) if item["label"] == label)


def test_hospital_bag_completion_finalizer_appends_explanation_before_link_and_is_idempotent() -> None:
    link = "**[打开待产包购物车](/hospital-bag-cart)**"
    original = "待产包清单已生成。"

    completed = ensure_hospital_bag_completion_followup(original)

    assert completed.startswith(original)
    assert "我只保留和孕周、喂养、医院确认真正相关的非常规物品" in completed
    assert "不用一次买完" in completed
    assert completed.index("不用一次买完") < completed.index(link)
    assert completed.endswith(link)
    assert ensure_hospital_bag_completion_followup(completed) == completed


def test_hospital_bag_completion_finalizer_uses_structured_card_decisions() -> None:
    card = {
        "owner": {
            "birth_path": "剖宫产",
            "feeding_intention": "混合",
            "return_to_work_timing": "6周后返工",
            "fetus_count": "双胎",
        },
        "packing_groups": [
            {
                "items": [
                    {"label": "高腰宽松内裤"},
                    {"label": "便携式吸奶器"},
                    {"label": "冷藏包/冰袋"},
                ]
            }
        ],
    }

    completed = ensure_hospital_bag_completion_followup("待产包清单已生成。", card=card)

    assert "考虑到你倾向剖宫产" in completed
    assert "考虑到你准备母乳或混合喂养" in completed
    assert "冷藏包/冰袋" in completed
    assert "考虑到这次是双胎" in completed
    assert completed.index("特殊物品我按这几个情况做了取舍") < completed.index("不用一次买完")
    assert completed.index("不用一次买完") < completed.index("/hospital-bag-cart")


def test_hospital_bag_card_personalizes_all_legacy_high_impact_inputs() -> None:
    card = build_hospital_bag_card_json(
        {
            "due_date_or_week": "36周",
            "first_birth": "否",
            "fetus_count": "双胎",
            "pregnancy_history_or_notes": ["妊娠糖尿病", "血压或子痫前期风险", "早产风险"],
            "medical_notes": "胎盘问题，遵循医生提醒",
            "birth_path": "剖宫产",
            "feeding_intention": "混合喂养",
            "return_to_work_timing": "6周后返工",
            "support_person": "支持少",
            "top_worries": ["不知道什么时候去医院", "怕母乳不够", "怕宝宝用品准备不全"],
        }
    )

    assert card["generation_mode"] == "packing"
    assert {group["group_id"] for group in card["packing_groups"]} >= {
        "documents",
        "mom_hospital_bag",
        "baby_discharge_bag",
        "support_person_bag",
        "car_backup_bag",
        "postpartum_home_first_week",
    }
    assert _item(card, "宝宝出院衣物")["quantity"] == "2套"
    assert _item(card, "高腰宽松内裤")["priority"] == "recommended"
    assert _item(card, "血糖记录/饮食医嘱")["priority"] == "must"
    assert _item(card, "血压记录/用药清单")["priority"] == "must"
    assert _item(card, "近期B超/医生医嘱")["priority"] == "must"
    assert _item(card, "冷藏包/冰袋")["priority"] == "recommended"
    assert _item(card, "便携式吸奶器")["priority"] == "must"
    assert any(
        source["field"] == "return_to_work_timing" for source in _item(card, "便携式吸奶器")["personalized_by"]
    )
    assert _item(card, "远程联系人名单")["priority"] == "must"
    assert "personalized_by" not in _item(card, "远程联系人名单")
    assert _item(card, "防溢乳垫")["explain"] == "放在内衣里吸收漏奶，避免衣服被打湿。"
    assert _item(card, "胎监带")["explain"] == "做胎心监护时固定探头用，有些医院要求自带。"
    assert _item(card, "收腹带")["explain"] == "产后腹部支撑用品，剖宫产尤其要先问医生。"
    assert any("收腹带" in question for question in card["hospital_questions"])
    assert any("母乳喂养支持" in question for question in card["hospital_questions"])
    assert any(source["field"] == "fetus_count" for source in _item(card, "宝宝出院衣物")["personalized_by"])
    followup = build_hospital_bag_followup(card)["message"]
    assert "考虑到你倾向剖宫产" in followup
    assert "冷藏包/冰袋" in followup
    assert "考虑到这次是双胎" in followup
    assert followup.endswith("**[打开待产包购物车](/hospital-bag-cart)**")


def test_hospital_bag_card_never_recommends_breastfeeding_items_for_formula_only_feeding() -> None:
    card = build_hospital_bag_card_json(
        {
            "due_date_or_week": "38周",
            "first_birth": "是",
            "fetus_count": "单胎",
            "pregnancy_history_or_notes": ["没有"],
            "birth_path": "顺产",
            "feeding_intention": "配方奶",
            "return_to_work_timing": "3个月后",
            "support_person": "有人全天帮忙",
            "top_worries": ["怕漏买", "怕母乳不够"],
        },
        generation_mode="immediate",
    )

    labels = {item["label"] for item in _items(card)}
    assert card["generation_mode"] == "immediate"
    assert {"奶瓶", "配方奶", "奶瓶清洁用品"} <= labels
    assert labels.isdisjoint({"便携式吸奶器", "哺乳文胸/哺乳背心", "防溢乳垫", "储奶袋/储奶瓶", "乳头霜", "乳盾"})
    assert any("奶瓶/配方奶" in question for question in card["hospital_questions"])
    assert not any("母乳喂养支持" in question for question in card["hospital_questions"])
    followup = build_hospital_bag_followup(card)["message"]
    assert "没有加入吸奶器、乳垫等母乳喂养用品" in followup


def test_hospital_bag_card_filters_hospital_provided_items_and_surfaces_the_impact() -> None:
    card = build_hospital_bag_card_json(
        {
            "due_date_or_week": "33周",
            "first_birth": "是",
            "fetus_count": "单胎",
            "pregnancy_history_or_notes": ["没有"],
            "birth_path": "顺产",
            "feeding_intention": "还不确定",
            "return_to_work_timing": "暂不返工",
            "support_person": "有人全天帮忙",
            "top_worries": ["怕漏买"],
            "hospital_provided_items": ["纸尿裤", "产褥垫"],
        }
    )

    labels = {item["label"] for item in _items(card)}
    assert "纸尿裤" not in labels
    assert "产褥垫/产妇卫生巾" not in labels
    assert card["hospital_context"]["hospital_provided_items"] == ["纸尿裤", "产褥垫"]
    assert "已从清单移除医院提供的物品：纸尿裤、产褥垫。" in card["personalized_notes"]
