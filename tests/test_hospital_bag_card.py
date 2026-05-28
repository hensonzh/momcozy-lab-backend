from __future__ import annotations

import unittest

from momcozy_agent.tool_handlers.cards import create_card, create_hospital_bag_card, create_hospital_bag_form


class HospitalBagCardTests(unittest.TestCase):
    def test_hospital_bag_form_submit_label_is_submit(self) -> None:
        result = create_hospital_bag_form({}, {"user_message": ""})

        self.assertEqual(result["form"]["id"], "hospital_bag_intake")
        self.assertEqual(result["form"]["submit_label"], "提交")

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
        self.assertIn("你的待产包已经设计好了哦", result["assistant_followup"]["message"])
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
            {"user_message": 'confirmed_form_data:\n{"feeding_intention": "配方"}'},
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


if __name__ == "__main__":
    unittest.main()
