from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from momcozy_agent.api_app import create_app
from momcozy_agent.services import data_store


class PregnancyDiaryApiTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["ENTRY_API_KEY"] = "test-token"
        self.db_path = Path(tempfile.mkdtemp(prefix="momcozy-diary-api-")) / "milk_management.db"
        os.environ["MILK_DB_PATH"] = str(self.db_path)
        data_store.DB_PATH = self.db_path  # type: ignore[attr-defined]
        self.client = TestClient(create_app())
        self.headers = {"Authorization": "Bearer test-token"}

    def test_create_list_today_and_update_diary_entry(self) -> None:
        create_response = self.client.post(
            "/v1/pregnancy-diary/create",
            json={
                "user_id": "u-diary",
                "entry_date": "2026-06-09",
                "gestational_week": "孕 32 周",
                "mood": "平稳",
                "energy_level": "一般",
                "sleep_summary": "夜里醒了两次",
                "fetal_movement": "胎动正常",
                "symptom_tags": ["水肿", "腰酸"],
                "content": "今天散步 20 分钟，心情还可以。",
            },
            headers=self.headers,
        )

        self.assertEqual(create_response.status_code, 200)
        payload = create_response.json()
        self.assertEqual(payload["error"], 0)
        entry_id = payload["diary"]["entry_id"]

        today_response = self.client.get(
            "/v1/pregnancy-diary/today",
            params={"user_id": "u-diary", "timestamp": "2026-06-09"},
            headers=self.headers,
        )
        self.assertEqual(today_response.json()["diary"]["entry_id"], entry_id)

        update_response = self.client.post(
            "/v1/pregnancy-diary/update",
            json={
                "user_id": "u-diary",
                "entry_id": entry_id,
                "entry_date": "2026-06-09",
                "mood": "有点焦虑",
                "energy_level": "偏低",
                "symptom_tags": ["腰酸"],
                "content": "下午有点累，准备早点休息。",
            },
            headers=self.headers,
        )
        self.assertEqual(update_response.json()["error"], 0)
        self.assertEqual(update_response.json()["diary"]["mood"], "有点焦虑")

        list_response = self.client.get(
            "/v1/pregnancy-diary/list",
            params={"user_id": "u-diary", "limit": 10},
            headers=self.headers,
        )
        diaries = list_response.json()["diary_list"]
        self.assertEqual(len(diaries), 1)
        self.assertEqual(diaries[0]["symptom_tags"], ["腰酸"])

    def test_read_endpoints_do_not_seed_demo_diary_data(self) -> None:
        list_response = self.client.get(
            "/v1/pregnancy-diary/list",
            params={"user_id": "u-empty", "limit": 10},
            headers=self.headers,
        )
        today_response = self.client.get(
            "/v1/pregnancy-diary/today",
            params={"user_id": "u-empty", "timestamp": "2026-06-09"},
            headers=self.headers,
        )

        self.assertEqual(list_response.json()["diary_list"], [])
        self.assertIsNone(today_response.json()["diary"])
        self.assertEqual(data_store.list_pregnancy_diary_entries(user_id="u-empty", limit=10), [])

    def test_rejects_missing_user(self) -> None:
        response = self.client.post(
            "/v1/pregnancy-diary/create",
            json={"entry_date": "2026-06-09", "content": "无 user"},
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"error": -1, "diary": None})


if __name__ == "__main__":
    unittest.main()
