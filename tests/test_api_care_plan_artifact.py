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
            title="生产全过程计划",
            summary="孕晚期生产准备",
            payload={"title": "生产全过程计划", "phases": []},
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


if __name__ == "__main__":
    unittest.main()
