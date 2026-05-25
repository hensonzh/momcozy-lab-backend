from __future__ import annotations

import unittest

from momcozy_agent.tool_handlers.cards import create_card


class HospitalBagCardTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
