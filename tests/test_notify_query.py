from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ["ENTRY_API_KEY"] = "test-token"
NOTIFY_DB_PATH = os.path.join(tempfile.mkdtemp(prefix="momcozy-agent-notify-tests-"), "milk_management.db")
os.environ["MILK_DB_PATH"] = NOTIFY_DB_PATH

from momcozy_agent.api.routes import mock_notify_list
from momcozy_agent.services import data_store


class NotifyQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["MILK_DB_PATH"] = NOTIFY_DB_PATH
        data_store.DB_PATH = Path(NOTIFY_DB_PATH)  # type: ignore[attr-defined]
        data_store.init_db()
        data_store.upsert_pump_health("u-health", 0, 0)

    def test_health_issue_notice_appears_when_pump_health_is_abnormal(self) -> None:
        data_store.upsert_pump_health("u-health", 1, 0)

        notify_list = mock_notify_list("u-health", "2026-06-12 11:30:00")

        health_items = [item for item in notify_list if item.get("event") == "health_issue"]
        self.assertEqual(len(health_items), 1)
        self.assertEqual(health_items[0]["time"], "11:30")
        self.assertEqual(health_items[0]["message"], "嗨，我发现你的乳汁电导率有点异常，可以和你聊聊吗")

    def test_health_issue_notice_is_hidden_when_pump_health_is_normal(self) -> None:
        notify_list = mock_notify_list("u-health", "2026-06-12 11:30:00")

        self.assertFalse(any(item.get("event") == "health_issue" for item in notify_list))


if __name__ == "__main__":
    unittest.main()
