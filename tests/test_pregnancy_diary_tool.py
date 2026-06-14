from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from momcozy_agent.agents import model_tool_output
from momcozy_agent.contexts import build_request_context
from momcozy_agent.services import data_store
from momcozy_agent.tool_handlers.pregnancy_diary import manage_pregnancy_diary


class PregnancyDiaryToolTests(unittest.TestCase):
    def test_list_seeds_demo_entries_into_database(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                result = manage_pregnancy_diary(
                    {
                        "action": "list",
                        "entry_id": None,
                        "entry_date": None,
                        "start_date": None,
                        "end_date": None,
                        "limit": 7,
                        "gestational_week": None,
                        "mood": None,
                        "energy_level": None,
                        "sleep_summary": None,
                        "fetal_movement": None,
                        "symptom_tags": None,
                        "appointment_note": None,
                        "nutrition_note": None,
                        "content": None,
                        "confirmed": False,
                    },
                    {"user_id": "app-user", "message_sent_at": "2026-06-10T09:00:00+08:00", "user_message": ""},
                )

                self.assertEqual(result["status"], "diary_list_read")
                self.assertEqual(len(result["diary_list"]), 7)
                entries = data_store.list_pregnancy_diary_entries(user_id="app-user", limit=10)
                self.assertEqual(len(entries), 7)
                for entry in entries:
                    data_store.delete_pregnancy_diary_entry(user_id="app-user", entry_id=entry["entry_id"])

                second = manage_pregnancy_diary(
                    {
                        "action": "list",
                        "entry_id": None,
                        "entry_date": None,
                        "start_date": None,
                        "end_date": None,
                        "limit": 7,
                        "gestational_week": None,
                        "mood": None,
                        "energy_level": None,
                        "sleep_summary": None,
                        "fetal_movement": None,
                        "symptom_tags": None,
                        "appointment_note": None,
                        "nutrition_note": None,
                        "content": None,
                        "confirmed": False,
                    },
                    {"user_id": "app-user", "message_sent_at": "2026-06-10T09:00:00+08:00", "user_message": ""},
                )
                self.assertEqual(second["diary_list"], [])
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_update_merges_only_supplied_fields(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                saved = data_store.save_pregnancy_diary_entry(
                    user_id="app-user",
                    entry_date="2026-06-10",
                    mood="平稳",
                    fetal_movement="胎动正常",
                    content="今天走路有点累。",
                )
                self.assertIsNotNone(saved)

                result = manage_pregnancy_diary(
                    {
                        "action": "update",
                        "entry_id": saved["entry_id"],
                        "entry_date": None,
                        "start_date": None,
                        "end_date": None,
                        "limit": 7,
                        "gestational_week": None,
                        "mood": "开心",
                        "energy_level": None,
                        "sleep_summary": None,
                        "fetal_movement": None,
                        "symptom_tags": None,
                        "appointment_note": None,
                        "nutrition_note": None,
                        "content": None,
                        "confirmed": False,
                    },
                    {"user_id": "app-user", "message_sent_at": "2026-06-10T09:00:00+08:00", "user_message": ""},
                )

                self.assertEqual(result["status"], "diary_entry_updated")
                self.assertEqual(result["diary"]["mood"], "开心")
                self.assertEqual(result["diary"]["fetal_movement"], "胎动正常")
                self.assertEqual(result["diary"]["content"], "今天走路有点累。")
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_delete_requires_confirmation(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                saved = data_store.save_pregnancy_diary_entry(user_id="app-user", entry_date="2026-06-10", content="今天记录。")
                self.assertIsNotNone(saved)
                base_args = {
                    "action": "delete",
                    "entry_id": saved["entry_id"],
                    "entry_date": None,
                    "start_date": None,
                    "end_date": None,
                    "limit": 7,
                    "gestational_week": None,
                    "mood": None,
                    "energy_level": None,
                    "sleep_summary": None,
                    "fetal_movement": None,
                    "symptom_tags": None,
                    "appointment_note": None,
                    "nutrition_note": None,
                    "content": None,
                }

                needs_confirm = manage_pregnancy_diary(
                    {**base_args, "confirmed": False},
                    {"user_id": "app-user", "message_sent_at": "2026-06-10T09:00:00+08:00", "user_message": ""},
                )
                self.assertEqual(needs_confirm["status"], "needs_delete_confirmation")
                self.assertIsNotNone(data_store.get_pregnancy_diary_entry(user_id="app-user", entry_id=saved["entry_id"]))

                deleted = manage_pregnancy_diary(
                    {**base_args, "confirmed": True},
                    {"user_id": "app-user", "message_sent_at": "2026-06-10T09:00:00+08:00", "user_message": ""},
                )
                self.assertEqual(deleted["status"], "diary_entry_deleted")
                self.assertIsNone(data_store.get_pregnancy_diary_entry(user_id="app-user", entry_id=saved["entry_id"]))
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_request_context_includes_lightweight_diary_summary(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                data_store.save_pregnancy_diary_entry(
                    user_id="app-user",
                    entry_date="2026-06-10",
                    gestational_week="孕32周",
                    mood="平稳",
                    fetal_movement="胎动正常",
                    symptom_tags=["腰酸"],
                    appointment_note="想问水肿是否需要控制盐分？",
                    content="今天下午走路有点累。",
                )

                context = build_request_context(
                    {"user_id": "app-user", "message_sent_at": "2026-06-10T09:00:00+08:00", "user_message": "帮我整理孕期日记"}
                )

                self.assertIn("pregnancy_diary_context:", context)
                self.assertIn("appointment_questions=1", context)
                self.assertIn("recent_tags=腰酸", context)
                self.assertIn("pregnancy_diary_manage", context)
                self.assertNotIn("今天下午走路有点累", context)
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_model_output_instructs_created_diary_final_reply_without_followup(self) -> None:
        compact = model_tool_output(
            {
                "ok": True,
                "tool_name": "pregnancy_diary_manage",
                "result": {
                    "ok": True,
                    "tool_name": "pregnancy_diary_manage",
                    "status": "diary_entry_created",
                    "action": "create",
                    "side_effect_performed": True,
                    "summary": "2026-06-10，孕32周，心情平稳",
                    "diary": {
                        "entry_id": 7,
                        "entry_date": "2026-06-10",
                        "content": "今天走路有点累，胎动正常。",
                    },
                },
            }
        )

        self.assertIn("final_response_instruction", compact)
        self.assertIn("孕期日记已经记录", compact["final_response_instruction"])
        self.assertIn("宝宝和我页面的孕期日记模块查看", compact["final_response_instruction"])
        self.assertEqual(compact["diary"]["entry_id"], 7)
        self.assertNotIn("assistant_followup", compact)
        self.assertNotIn("content", compact["diary"])

    def test_model_output_instructs_updated_diary_final_reply_without_followup(self) -> None:
        compact = model_tool_output(
            {
                "ok": True,
                "tool_name": "pregnancy_diary_manage",
                "result": {
                    "ok": True,
                    "tool_name": "pregnancy_diary_manage",
                    "status": "diary_entry_updated",
                    "action": "update",
                    "side_effect_performed": True,
                    "summary": "2026-06-10，补充了产检问题",
                    "diary": {
                        "entry_id": 8,
                        "entry_date": "2026-06-10",
                        "appointment_note": "下次问医生睡眠不好怎么办。",
                    },
                },
            }
        )

        self.assertIn("final_response_instruction", compact)
        self.assertIn("孕期日记已经修改", compact["final_response_instruction"])
        self.assertIn("宝宝和我页面的孕期日记模块查看", compact["final_response_instruction"])
        self.assertEqual(compact["diary"]["entry_id"], 8)
        self.assertNotIn("assistant_followup", compact)
        self.assertNotIn("appointment_note", compact["diary"])


if __name__ == "__main__":
    unittest.main()
