from __future__ import annotations

import unittest

from momcozy_agent.contexts import build_request_context
from momcozy_agent.tool_handlers.cards import DEFAULT_HOSPITAL_BAG_CART_GROUPS, recommend_hospital_bag_pump, update_hospital_bag_cart


class HospitalBagCartToolTests(unittest.TestCase):
    def test_apply_budget_plan_reduces_total_and_preserves_pump(self) -> None:
        result = update_hospital_bag_cart(
            {
                "action": "apply_budget_plan",
                "item_ids": [],
                "quantity_updates": [],
                "target_budget": None,
                "budget_mode": "cheaper",
                "preference": "balanced",
                "preserve_item_ids": [],
                "allow_remove_pump": False,
                "assistant_message": "",
            },
            {"user_message": "太贵了，有没有便宜一点的方案"},
        )

        groups = result["cart_update"]["groups"]
        item_ids = [item["id"] for group in groups for item in group["items"]]
        self.assertIn("milk-pump", item_ids)
        self.assertNotIn("baby-blanket", item_ids)
        self.assertIn("baby-blanket-basic", item_ids)
        self.assertLess(result["cart_update"]["totals"]["total"], result["cart_update"]["before_totals"]["total"])

    def test_target_budget_under_1000_preserves_pump_when_possible(self) -> None:
        result = update_hospital_bag_cart(
            {
                "action": "optimize_budget",
                "item_ids": [],
                "quantity_updates": [],
                "target_budget": 1000,
                "budget_mode": "under",
                "preference": "balanced",
                "preserve_item_ids": [],
                "allow_remove_pump": False,
                "assistant_message": "",
            },
            {"user_message": "我的预算在1000元以内"},
        )

        groups = result["cart_update"]["groups"]
        item_ids = [item["id"] for group in groups for item in group["items"]]
        self.assertIn("milk-pump", item_ids)
        self.assertTrue(result["cart_update"]["budget_met"])
        self.assertLessEqual(result["cart_update"]["totals"]["total"], 1000)
        self.assertGreater(result["cart_update"]["totals"]["total"], 900)
        self.assertIn("吸奶器我先保留", result["summary"])

    def test_very_low_budget_does_not_remove_pump_without_permission(self) -> None:
        result = update_hospital_bag_cart(
            {
                "action": "optimize_budget",
                "item_ids": [],
                "quantity_updates": [],
                "target_budget": 800,
                "budget_mode": "under",
                "preference": "balanced",
                "preserve_item_ids": [],
                "allow_remove_pump": False,
                "assistant_message": "",
            },
            {"user_message": "预算800以内"},
        )

        item_ids = [item["id"] for group in result["cart_update"]["groups"] for item in group["items"]]
        self.assertIn("milk-pump", item_ids)
        self.assertFalse(result["cart_update"]["budget_met"])
        self.assertGreater(result["cart_update"]["totals"]["total"], 800)

    def test_remove_items_uses_current_cart(self) -> None:
        result = update_hospital_bag_cart(
            {
                "action": "remove_items",
                "item_ids": ["milk-pump"],
                "quantity_updates": [],
                "target_budget": None,
                "budget_mode": "none",
                "preference": "balanced",
                "preserve_item_ids": [],
                "allow_remove_pump": False,
                "assistant_message": "",
            },
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

    def test_mark_provided_removes_hospital_items(self) -> None:
        result = update_hospital_bag_cart(
            {
                "action": "mark_provided",
                "item_ids": ["baby-diaper"],
                "quantity_updates": [],
                "target_budget": None,
                "budget_mode": "none",
                "preference": "balanced",
                "preserve_item_ids": [],
                "allow_remove_pump": False,
                "assistant_message": "",
            },
            {
                "user_message": "医院会提供纸尿裤",
                "hospital_bag_cart": {"groups": DEFAULT_HOSPITAL_BAG_CART_GROUPS},
            },
        )

        item_ids = [item["id"] for group in result["cart_update"]["groups"] for item in group["items"]]
        self.assertNotIn("baby-diaper", item_ids)
        self.assertIn("医院会提供", result["summary"])

    def test_reset_cart_includes_real_product_images(self) -> None:
        result = update_hospital_bag_cart(
            {
                "action": "reset_cart",
                "item_ids": [],
                "quantity_updates": [],
                "target_budget": None,
                "budget_mode": "none",
                "preference": "balanced",
                "preserve_item_ids": [],
                "allow_remove_pump": False,
                "assistant_message": "",
            },
            {"user_message": "恢复默认购物车"},
        )

        items = [item for group in result["cart_update"]["groups"] for item in group["items"]]
        diaper = next(item for item in items if item["id"] == "baby-diaper")
        cream = next(item for item in items if item["id"] == "milk-cream")
        pump = next(item for item in items if item["id"] == "milk-pump")
        self.assertTrue(diaper["image_url"].startswith("https://babycozy.com/cdn/shop/files/"))
        self.assertTrue(cream["image_url"].startswith("https://momcozy.com/cdn/shop/files/"))
        self.assertTrue(pump["image_url"].startswith("https://momcozy.com/cdn/shop/files/"))

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

    def test_pump_recommendation_uses_official_price_and_suggests_cart_sync(self) -> None:
        result = recommend_hospital_bag_pump(
            {
                "use_case": "work_pumping",
                "preference": "app",
                "feeding_intention": "breastfeeding",
                "target_budget_usd": None,
                "must_have_app": True,
                "need_single_unit": None,
                "assistant_message": "",
            },
            {"user_message": "我上班后要用，想要能连App的"},
        )

        product = result["recommended_product"]
        self.assertEqual(result["status"], "pump_recommended")
        self.assertIn(product["sku_id"], {"pump-m5-smart", "pump-m9", "pump-air-1"})
        self.assertTrue(product["price_label"].startswith("¥"))
        self.assertIn("official_price_usd", product)
        self.assertEqual(product["exchange_rate_usd_cny"], 6.8)
        self.assertTrue(product["image_url"].startswith("https://momcozy.com/cdn/shop/files/"))
        self.assertEqual(result["cart_sync_suggestion"]["action"], "replace_pump_model")

    def test_replace_pump_model_updates_cart_with_official_price_converted_to_cny(self) -> None:
        result = update_hospital_bag_cart(
            {
                "action": "replace_pump_model",
                "item_ids": [],
                "product_sku_id": "pump-s12-pro-quick",
                "quantity_updates": [],
                "target_budget": None,
                "budget_mode": "none",
                "preference": "balanced",
                "preserve_item_ids": [],
                "allow_remove_pump": False,
                "assistant_message": "",
            },
            {
                "user_message": "换成S12 Pro Quick",
                "hospital_bag_cart": {"groups": DEFAULT_HOSPITAL_BAG_CART_GROUPS},
            },
        )

        items = [item for group in result["cart_update"]["groups"] for item in group["items"]]
        item_ids = [item["id"] for item in items]
        pump = next(item for item in items if item["id"] == "pump-s12-pro-quick")
        self.assertNotIn("milk-pump", item_ids)
        self.assertEqual(pump["currency"], "CNY")
        self.assertEqual(pump["price"], 509.93)
        self.assertEqual(pump["price_label"], "¥509.93")
        self.assertEqual(pump["official_price_usd"], 74.99)
        self.assertEqual(pump["exchange_rate_usd_cny"], 6.8)
        self.assertTrue(pump["image_url"].startswith("https://momcozy.com/cdn/shop/files/"))
        self.assertFalse(result["cart_update"]["totals"]["mixed_currency"])
        self.assertEqual(result["cart_update"]["totals"]["currency_totals"][0]["currency"], "CNY")

    def test_budget_optimizer_protects_concrete_pump_model(self) -> None:
        replaced = update_hospital_bag_cart(
            {
                "action": "replace_pump_model",
                "item_ids": [],
                "product_sku_id": "pump-m9",
                "quantity_updates": [],
                "target_budget": None,
                "budget_mode": "none",
                "preference": "balanced",
                "preserve_item_ids": [],
                "allow_remove_pump": False,
                "assistant_message": "",
            },
            {"user_message": "换成M9", "hospital_bag_cart": {"groups": DEFAULT_HOSPITAL_BAG_CART_GROUPS}},
        )
        optimized = update_hospital_bag_cart(
            {
                "action": "optimize_budget",
                "item_ids": [],
                "product_sku_id": None,
                "quantity_updates": [],
                "target_budget": 800,
                "budget_mode": "under",
                "preference": "balanced",
                "preserve_item_ids": [],
                "allow_remove_pump": False,
                "assistant_message": "",
            },
            {"user_message": "预算800以内", "hospital_bag_cart": {"groups": replaced["cart_update"]["groups"]}},
        )

        item_ids = [item["id"] for group in optimized["cart_update"]["groups"] for item in group["items"]]
        self.assertIn("pump-m9", item_ids)


if __name__ == "__main__":
    unittest.main()
