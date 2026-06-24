from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from momcozy_agent.api_app import create_app
from momcozy_agent.services import data_store


class CarePlanArtifactApiTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["ENTRY_API_KEY"] = "test-token"
        self.db_path = Path(tempfile.mkdtemp(prefix="momcozy-plan-api-")) / "milk_management.db"
        os.environ["MILK_DB_PATH"] = str(self.db_path)
        data_store.DB_PATH = self.db_path  # type: ignore[attr-defined]
        self.client = TestClient(create_app())
        self.headers = {"Authorization": "Bearer test-token"}

    def test_delete_artifact_soft_deletes_birth_journey_plan(self) -> None:
        saved = data_store.save_care_plan_artifact(
            user_id="u-plan",
            plan_type="birth_journey",
            title="孕期计划",
            summary="孕晚期生产准备",
            payload={"title": "孕期计划", "phases": []},
            source_artifact_type="birth_journey_plan_card",
        )
        self.assertIsNotNone(saved)
        plan_id = int(saved["plan_id"])

        delete_response = self.client.post(
            "/v1/plan/delete-artifact",
            json={"user_id": "u-plan", "plan_id": plan_id},
            headers=self.headers,
        )

        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.json(), {"error": 0})

        active_response = self.client.get(
            "/v1/plan/list",
            params={"user_id": "u-plan", "status": "active"},
            headers=self.headers,
        )
        self.assertEqual(active_response.json()["plan_list"], [])

        deleted_response = self.client.get(
            "/v1/plan/list",
            params={"user_id": "u-plan", "status": "deleted"},
            headers=self.headers,
        )
        deleted_plans = deleted_response.json()["plan_list"]
        self.assertEqual(len(deleted_plans), 1)
        self.assertEqual(deleted_plans[0]["status"], "deleted")
        self.assertEqual(deleted_plans[0]["plan_id"], plan_id)

    def test_delete_artifact_rejects_unknown_plan(self) -> None:
        response = self.client.post(
            "/v1/plan/delete-artifact",
            json={"user_id": "u-plan", "plan_id": 999},
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"error": -1})

    def test_birth_journey_plan_list_and_detail_normalize_legacy_todo_plan(self) -> None:
        saved = data_store.save_care_plan_artifact(
            user_id="u-plan",
            plan_type="birth_journey",
            title="孕期计划",
            summary="旧版孕期计划",
            payload={
                "title": "孕期计划",
                "todo_engine_version": "actionable_v2",
                "todo_plan": {
                    "periods": [
                        {
                            "id": "period_01",
                            "title": "孕 29-32 周",
                            "items": [
                                {
                                    "id": "old_01",
                                    "title": "建立胎动和异常联系机制",
                                    "reason": "重要｜考虑到你进入孕晚期，固定观察节奏很重要。",
                                    "priority_type": "essential",
                                    "priority_label": "重要",
                                    "steps": ["定观察时间"],
                                }
                            ],
                        }
                    ]
                },
            },
            source_artifact_type="birth_journey_plan_card",
        )

        list_response = self.client.get(
            "/v1/plan/list",
            params={"user_id": "u-plan", "status": "active"},
            headers=self.headers,
        )
        detail_response = self.client.get(
            "/v1/plan/detail",
            params={"user_id": "u-plan", "plan_id": saved["plan_id"]},
            headers=self.headers,
        )

        listed_plan = list_response.json()["plan_list"][0]
        detailed_plan = detail_response.json()["plan"]
        for plan in (listed_plan, detailed_plan):
            payload = plan["payload"]
            self.assertEqual(payload["todo_engine_version"], "actionable_steps_v2")
            self.assertNotIn("planning_layers", payload)
            todo_item = payload["todo_plan"]["periods"][0]["items"][0]
            self.assertEqual(todo_item["title"], "每天固定看胎动和不舒服")
            self.assertFalse(todo_item["completed"])

    def test_dev_startup_reset_only_deletes_active_birth_journey_plans(self) -> None:
        birth_plan = data_store.save_care_plan_artifact(
            user_id="u-plan",
            plan_type="birth_journey",
            title="孕期计划",
            summary="孕晚期生产准备",
            payload={"title": "孕期计划", "phases": []},
            source_artifact_type="birth_journey_plan_card",
        )
        other_plan = data_store.save_care_plan_artifact(
            user_id="u-plan",
            plan_type="milk_management",
            title="奶量管理计划",
            summary="稳奶计划",
            payload={"title": "奶量管理计划"},
            source_artifact_type="milk_plan_card",
        )

        cleared = data_store.reset_birth_journey_care_plans_for_dev()

        self.assertEqual(cleared, 1)
        self.assertEqual(data_store.get_care_plan_artifact(user_id="u-plan", plan_id=int(birth_plan["plan_id"]))["status"], "deleted")
        self.assertEqual(data_store.get_care_plan_artifact(user_id="u-plan", plan_id=int(other_plan["plan_id"]))["status"], "active")


if __name__ == "__main__":
    unittest.main()
