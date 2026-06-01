from __future__ import annotations

import unittest

from momcozy_agent.tool_handlers.cards import create_card, create_form, create_hospital_bag_card, create_hospital_bag_form


class HospitalBagCardTests(unittest.TestCase):
    def test_hospital_bag_form_submit_label_is_submit(self) -> None:
        result = create_hospital_bag_form({}, {"user_message": ""})

        self.assertEqual(result["form"]["id"], "hospital_bag_intake")
        self.assertEqual(result["form"]["submit_label"], "提交")

    def test_hospital_bag_form_uses_revised_intake_questions(self) -> None:
        result = create_hospital_bag_form({}, {"user_message": ""})

        fields = result["form"]["fields"]
        field_ids = [field["id"] for field in fields]
        self.assertEqual(
            field_ids,
            [
                "due_date_or_week",
                "first_birth",
                "fetus_count",
                "pregnancy_history_or_notes",
                "birth_path",
                "feeding_intention",
                "return_to_work_timing",
                "support_person",
                "budget_preference",
                "top_worries",
            ],
        )
        self.assertNotIn("birth_setting", field_ids)
        self.assertNotIn("hospital_provided_items", field_ids)

        by_id = {field["id"]: field for field in fields}
        self.assertEqual(by_id["pregnancy_history_or_notes"]["options"][-1], "其它")
        self.assertTrue(by_id["pregnancy_history_or_notes"]["allow_other_input"])
        self.assertNotIn("不确定", by_id["pregnancy_history_or_notes"]["options"])
        self.assertNotIn("计划剖宫产", by_id["pregnancy_history_or_notes"]["options"])
        self.assertEqual(by_id["birth_path"]["label"], "生产信息｜分娩方式")
        self.assertEqual(by_id["feeding_intention"]["options"], ["亲喂母乳", "配方奶", "混合喂养", "还不确定"])
        self.assertEqual(by_id["return_to_work_timing"]["type"], "text")
        self.assertEqual(by_id["return_to_work_timing"]["label"], "喂养信息｜产后多久返工")
        self.assertEqual(by_id["budget_preference"]["options"], ["低预算", "中预算", "高预算"])
        self.assertEqual(by_id["budget_preference"]["label"], "偏好信息｜预算偏好")
        self.assertEqual(by_id["top_worries"]["label"], "偏好信息｜最焦虑的事")
        self.assertEqual(by_id["top_worries"]["options"][-1], "其它")
        self.assertTrue(by_id["top_worries"]["allow_other_input"])

    def test_hospital_bag_form_ignores_non_dialogue_default_values(self) -> None:
        result = create_hospital_bag_form(
            {
                "default_values": {
                    "first_birth": "是",
                    "fetus_count": "单胎",
                    "pregnancy_history_or_notes": ["没有"],
                    "birth_path": "顺产",
                    "feeding_intention": "母乳",
                    "support_person": "有人全天帮忙",
                }
            },
            {"user_message": ""},
        )

        by_id = {field["id"]: field for field in result["form"]["fields"]}
        self.assertNotIn("default_value", by_id["first_birth"])
        self.assertNotIn("default_value", by_id["fetus_count"])
        self.assertNotIn("default_value", by_id["pregnancy_history_or_notes"])
        self.assertNotIn("default_value", by_id["birth_path"])
        self.assertNotIn("default_value", by_id["feeding_intention"])
        self.assertNotIn("default_value", by_id["support_person"])
        self.assertEqual(result["form"]["default_values"], {})

    def test_generic_hospital_bag_form_strips_non_dialogue_field_defaults(self) -> None:
        result = create_form(
            {
                "form_id": "hospital_bag_intake",
                "title": "信息采集",
                "submit_label": "提交",
                "fields": [
                    {
                        "id": "due_date_or_week",
                        "label": "基本信息｜预产期或当前孕周",
                        "type": "text",
                        "required": True,
                        "default_value": "37 周",
                    },
                    {
                        "id": "first_birth",
                        "label": "基本信息｜是否第一胎",
                        "type": "select",
                        "required": True,
                        "default_value": "是",
                        "options": ["是", "否"],
                    },
                    {
                        "id": "feeding_intention",
                        "label": "喂养信息｜喂养意向",
                        "type": "select",
                        "required": True,
                        "default_value": "母乳",
                        "options": ["亲喂母乳", "配方奶", "混合喂养", "还不确定"],
                    },
                ],
            },
            {"user_message": ""},
        )

        by_id = {field["id"]: field for field in result["form"]["fields"]}
        self.assertEqual(by_id["due_date_or_week"]["default_value"], "37 周")
        self.assertNotIn("default_value", by_id["first_birth"])
        self.assertNotIn("default_value", by_id["feeding_intention"])

    def test_generic_form_with_hospital_bag_fields_is_normalized_to_hospital_bag_intake(self) -> None:
        result = create_form(
            {
                "form_id": "form",
                "title": "信息采集",
                "submit_label": "提交",
                "fields": [
                    {
                        "id": "due_date_or_week",
                        "label": "预产期或当前孕周",
                        "type": "text",
                        "required": True,
                        "default_value": "30周",
                    },
                    {
                        "id": "first_birth",
                        "label": "是否第一胎",
                        "type": "select",
                        "required": True,
                        "default_value": "是",
                        "options": ["是", "否"],
                    },
                    {
                        "id": "fetus_count",
                        "label": "这次是单胎、双胎，还是三胎及以上？",
                        "type": "select",
                        "required": True,
                        "default_value": "单胎",
                        "options": ["单胎", "双胎", "三胎及以上"],
                    },
                ],
            },
            {"user_message": ""},
        )

        self.assertEqual(result["form"]["id"], "hospital_bag_intake")
        by_id = {field["id"]: field for field in result["form"]["fields"]}
        self.assertEqual(by_id["due_date_or_week"]["default_value"], "30周")
        self.assertNotIn("default_value", by_id["first_birth"])
        self.assertNotIn("default_value", by_id["fetus_count"])
        self.assertIn("budget_preference", by_id)
        self.assertEqual(result["form"]["default_values"], {"due_date_or_week": "30周"})

    def test_hospital_bag_form_does_not_default_uncollected_fields(self) -> None:
        result = create_hospital_bag_form(
            {"default_values": {"due_date_or_week": "35 周"}},
            {"user_message": ""},
        )

        by_id = {field["id"]: field for field in result["form"]["fields"]}
        self.assertEqual(by_id["due_date_or_week"]["default_value"], "35 周")
        self.assertNotIn("default_value", by_id["first_birth"])
        self.assertNotIn("default_value", by_id["birth_path"])
        self.assertNotIn("default_value", by_id["feeding_intention"])
        self.assertNotIn("default_value", by_id["budget_preference"])

    def test_hospital_bag_form_prefills_pre_dialogue_fields_when_collected(self) -> None:
        result = create_hospital_bag_form(
            {
                "default_values": (
                    '{"due_date_or_week":"35 周","return_to_work_timing":"6 周后",'
                    '"budget_preference":"中预算","top_worries":["怕漏买","怕母乳不够","怕产后没人帮"],'
                    '"feeding_intention":"母乳"}'
                )
            },
            {"user_message": ""},
        )

        field_ids = [field["id"] for field in result["form"]["fields"]]
        self.assertIn("due_date_or_week", field_ids)
        self.assertIn("return_to_work_timing", field_ids)
        self.assertIn("budget_preference", field_ids)
        self.assertIn("top_worries", field_ids)
        self.assertIn("first_birth", field_ids)
        self.assertIn("feeding_intention", field_ids)
        by_id = {field["id"]: field for field in result["form"]["fields"]}
        self.assertEqual(by_id["due_date_or_week"]["default_value"], "35 周")
        self.assertEqual(by_id["return_to_work_timing"]["default_value"], "6 周后")
        self.assertEqual(by_id["budget_preference"]["default_value"], "中预算")
        self.assertEqual(by_id["top_worries"]["default_value"], "怕漏买, 怕母乳不够, 怕产后没人帮")
        self.assertEqual(result["form"]["default_values"]["due_date_or_week"], "35 周")
        self.assertNotIn("feeding_intention", result["form"]["default_values"])
        self.assertNotIn("default_value", by_id["feeding_intention"])
        self.assertEqual(result["form"]["default_values"]["top_worries"], ["怕漏买", "怕母乳不够", "怕产后没人帮"])

    def test_adds_breast_pump_with_quantity_to_lactation_group(self) -> None:
        card_json = {
            "packing_groups": [
                {"group_id": "lactation", "title": "哺乳用品", "items": []},
            ]
        }

        result = create_card(
            {
                "card_type": "hospital_bag_card",
                "schema_version": "1.0",
                "card_json": card_json,
            },
            {"user_message": 'confirmed_form_data:\n{"feeding_intention": "母乳"}'},
        )

        items = result["card"]["card_json"]["packing_groups"][0]["items"]
        pump = next(item for item in items if "吸奶器" in item["label"])
        self.assertEqual(pump["quantity"], "1台")
        self.assertEqual(pump["priority"], "recommended")
        self.assertEqual(pump["explain"], "涨奶、排奶或回家后储奶时备用。")
        self.assertIn("assistant_followup", result)
        self.assertIn("已经根据你的情况为你制作了待产包。", result["assistant_followup"]["message"])
        self.assertIn("\n\n我顺手把清单里适合直接购买的", result["assistant_followup"]["message"])
        self.assertIn("/hospital-bag-cart", result["assistant_followup"]["message"])

    def test_formula_feeding_does_not_add_breast_pump(self) -> None:
        card_json = {
            "packing_groups": [
                {"group_id": "lactation", "title": "哺乳用品", "items": []},
            ]
        }

        result = create_card(
            {
                "card_type": "hospital_bag_card",
                "schema_version": "1.0",
                "card_json": card_json,
            },
            {"user_message": 'confirmed_form_data:\n{"feeding_intention": "配方奶"}'},
        )

        items = result["card"]["card_json"]["packing_groups"][0]["items"]
        self.assertEqual(items, [])
        self.assertIn("assistant_followup", result)
        self.assertIn("/hospital-bag-cart", result["assistant_followup"]["message"])

    def test_adds_postpartum_group_when_missing(self) -> None:
        card_json = {
            "packing_groups": [
                {"group_id": "documents", "title": "证件资料", "items": []},
                {"group_id": "baby", "title": "宝宝出院包", "items": []},
            ]
        }

        result = create_card(
            {
                "card_type": "hospital_bag_card",
                "schema_version": "1.0",
                "card_json": card_json,
            },
            {"user_message": 'confirmed_form_data:\n{"feeding_intention": "母乳"}'},
        )

        groups = result["card"]["card_json"]["packing_groups"]
        postpartum = next(group for group in groups if group["group_id"] == "postpartum_home_first_week")
        self.assertEqual(postpartum["title"], "产后回家第一周用品")
        self.assertTrue(any("吸奶器" in item["label"] for item in postpartum["items"]))

    def test_generated_card_adds_uncommon_item_explanations(self) -> None:
        result = create_hospital_bag_card(
            {
                "confirmed_form_data": {
                    "due_date_or_week": "32 周",
                    "birth_path": "剖宫产",
                    "feeding_intention": "母乳",
                    "support_person": "有，且需要准备物品",
                }
            },
            {"user_message": ""},
        )

        groups = result["card"]["card_json"]["packing_groups"]
        items = [item for group in groups for item in group["items"]]
        labels = [item["label"] for item in items]
        breast_pad = next(item for item in items if item["label"] == "防溢乳垫")
        fetal_monitor_band = next(item for item in items if item["label"] == "胎监带")
        belly_band = next(item for item in items if item["label"] == "收腹带")
        identity_document = next(item for item in items if item["label"] == "身份证件")
        birth_communication_card = next(item for item in items if item["label"] == "分娩沟通卡")

        self.assertNotIn("润唇膏", labels)
        self.assertEqual(breast_pad["explain"], "放在内衣里吸收漏奶，避免衣服被打湿。")
        self.assertEqual(fetal_monitor_band["explain"], "做胎心监护时固定探头用，有些医院要求自带。")
        self.assertEqual(belly_band["explain"], "产后腹部支撑用品，剖宫产尤其要先问医生。")
        self.assertNotIn("note", identity_document)
        self.assertNotIn("explain", identity_document)
        self.assertEqual(
            birth_communication_card["explain"],
            "记录生产偏好和需要提前沟通的事，入院时方便给医护看。",
        )

    def test_generated_card_marks_field_driven_items(self) -> None:
        result = create_hospital_bag_card(
            {
                "confirmed_form_data": {
                    "due_date_or_week": "36 周",
                    "first_birth": "否",
                    "fetus_count": "双胎",
                    "pregnancy_history_or_notes": ["宝宝可能 NICU"],
                    "birth_path": "剖宫产",
                    "feeding_intention": "混合喂养",
                    "return_to_work_timing": "6 周后",
                    "support_person": "支持少",
                    "budget_preference": "低预算",
                    "top_worries": ["怕母乳不够", "怕宝宝用品准备不全"],
                }
            },
            {"user_message": ""},
        )

        groups = result["card"]["card_json"]["packing_groups"]
        items = [item for group in groups for item in group["items"]]
        high_waist = next(item for item in items if item["label"] == "高腰宽松内裤")
        baby_clothes = next(item for item in items if item["label"] == "宝宝出院衣物")
        pump = next(item for item in items if item["label"] == "便携式吸奶器")
        remote_contacts = next(item for item in items if item["label"] == "远程联系人名单")

        self.assertTrue(_has_source(high_waist, "birth_path", "剖宫产"))
        self.assertTrue(_has_source(baby_clothes, "fetus_count", "双胎"))
        self.assertTrue(_has_source(pump, "feeding_intention", "混合"))
        self.assertTrue(_has_source(pump, "return_to_work_timing", "6 周后"))
        self.assertNotIn("personalized_by", remote_contacts)

    def test_generated_card_suppresses_selected_personalization_reasons(self) -> None:
        result = create_hospital_bag_card(
            {
                "confirmed_form_data": {
                    "due_date_or_week": "36 周",
                    "first_birth": "是",
                    "fetus_count": "双胎",
                    "pregnancy_history_or_notes": ["妊娠糖尿病", "宝宝可能 NICU"],
                    "birth_path": "剖宫产",
                    "feeding_intention": "混合喂养",
                    "return_to_work_timing": "6 周后",
                    "support_person": "有人全天帮忙",
                    "budget_preference": "中预算",
                    "top_worries": ["不知道什么时候去医院", "怕宝宝用品准备不全"],
                }
            },
            {"user_message": ""},
        )

        groups = result["card"]["card_json"]["packing_groups"]
        items = [item for group in groups for item in group["items"]]
        by_label = {item["label"]: item for item in items}
        for label in [
            "检查报告/化验单",
            "医院预登记信息",
            "紧急联系人信息",
            "医生/医院联系电话",
            "手机充电线和充电器",
            "医院路线和停车信息",
            "夜间入口信息",
        ]:
            self.assertIn(label, by_label)
            self.assertNotIn("personalized_by", by_label[label])

        support_group = next(group for group in groups if group["group_id"] == "support_person_bag")
        self.assertTrue(support_group["items"])
        for item in support_group["items"]:
            self.assertNotIn("personalized_by", item)


def _has_source(item: dict, field: str, condition: str) -> bool:
    return any(
        source.get("field") == field and condition in source.get("condition", "")
        for source in item.get("personalized_by", [])
    )


if __name__ == "__main__":
    unittest.main()
