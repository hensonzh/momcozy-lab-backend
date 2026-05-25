from __future__ import annotations

import unittest

from momcozy_agent.contexts import build_request_context
from momcozy_agent.tool_handlers.cards import DEFAULT_HOSPITAL_BAG_CART_GROUPS, update_hospital_bag_cart


class HospitalBagCartToolTests(unittest.TestCase):
    def test_apply_budget_plan_reduces_total_and_replaces_blanket(self) -> None:
        result = update_hospital_bag_cart(
            {"action": "apply_budget_plan", "item_ids": [], "assistant_message": ""},
            {"user_message": "太贵了，有没有便宜一点的方案"},
        )

        groups = result["cart_update"]["groups"]
        item_ids = [item["id"] for group in groups for item in group["items"]]
        self.assertNotIn("milk-pump", item_ids)
        self.assertNotIn("baby-blanket", item_ids)
        self.assertIn("baby-blanket-basic", item_ids)
        self.assertLess(result["cart_update"]["totals"]["total"], 800)

    def test_remove_items_uses_current_cart(self) -> None:
        result = update_hospital_bag_cart(
            {"action": "remove_items", "item_ids": ["milk-pump"], "assistant_message": ""},
            {
                "user_message": "吸奶器先不要",
                "hospital_bag_cart": {"groups": DEFAULT_HOSPITAL_BAG_CART_GROUPS},
            },
        )

        groups = result["cart_update"]["groups"]
        item_ids = [item["id"] for group in groups for item in group["items"]]
        self.assertNotIn("milk-pump", item_ids)
        self.assertEqual(result["cart_update"]["removed_item_names"], ["便携式吸奶器"])
        self.assertIn("¥", result["summary"])

    def test_request_context_includes_cart_item_ids(self) -> None:
        context = build_request_context(
            {
                "user_message": "吸奶器先不要",
                "locale": "zh-CN",
                "hospital_bag_cart": {"groups": DEFAULT_HOSPITAL_BAG_CART_GROUPS, "totals": {"total": 1663.9, "itemCount": 18}},
            }
        )

        self.assertIn("current_hospital_bag_cart:", context)
        self.assertIn("item_id=milk-pump", context)
        self.assertIn("name=便携式吸奶器", context)


if __name__ == "__main__":
    unittest.main()
