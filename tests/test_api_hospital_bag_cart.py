from __future__ import annotations

import os
import unittest

from fastapi.testclient import TestClient

from momcozy_agent.api_app import create_app
from momcozy_agent.tool_handlers.cards import DEFAULT_HOSPITAL_BAG_CART_GROUPS


class HospitalBagCartApiTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["ENTRY_API_KEY"] = "test-token"
        self.client = TestClient(create_app())

    def test_direct_cart_update_requires_auth(self) -> None:
        response = self.client.post("/api/hospital-bag/cart-update", json={})

        self.assertEqual(response.status_code, 401)

    def test_direct_cart_update_replaces_pump_model_without_agent_loop(self) -> None:
        response = self.client.post(
            "/api/hospital-bag/cart-update",
            headers={"Authorization": "Bearer test-token"},
            json={
                "user_message": "好，换成 Air 1 吧",
                "hospital_bag_cart": {"groups": DEFAULT_HOSPITAL_BAG_CART_GROUPS},
                "args": {
                    "action": "replace_pump_model",
                    "product_sku_id": "pump-air-1",
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "cart_updated")
        items = [item for group in payload["cart_update"]["groups"] for item in group["items"]]
        item_ids = [item["id"] for item in items]
        self.assertIn("pump-air-1", item_ids)
        self.assertNotIn("milk-pump", item_ids)
        self.assertIn("Air 1", payload["summary"])


if __name__ == "__main__":
    unittest.main()
