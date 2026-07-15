from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from momcozy_agent.agents import model_tool_output, safe_tool_result
from momcozy_agent.contexts import build_request_context
from momcozy_agent.services import data_store
from momcozy_agent.tool_handlers.pregnancy_diary import manage_pregnancy_diary


class PregnancyDiaryToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_db_path = data_store.DB_PATH
        self.tmp = tempfile.TemporaryDirectory()
        data_store.DB_PATH = Path(self.tmp.name) / "milk_management.db"  # type: ignore[assignment]

    def tearDown(self) -> None:
        data_store.DB_PATH = self.old_db_path  # type: ignore[assignment]
        self.tmp.cleanup()

    def _inputs(self, message: str = "") -> dict[str, str]:
        return {
            "user_id": "app-user",
            "message_sent_at": "2026-06-10T09:00:00+08:00",
            "user_message": message,
        }

    def test_list_reads_existing_entries_without_seeding_demo_data(self) -> None:
        for offset, content in enumerate(["今天有点累。", "昨天睡得一般。"]):
            data_store.save_pregnancy_diary_entry(
                user_id="app-user",
                entry_date=f"2026-06-{10 - offset:02d}",
                content=content,
            )

        result = manage_pregnancy_diary(
            {"action": "list", "start_date": None, "end_date": None, "limit": 7, "content": None, "confirmed": False},
            self._inputs(),
        )

        self.assertEqual(result["status"], "diary_list_read")
        self.assertEqual(len(result["diary_list"]), 2)
        self.assertIn("最近读取 2 条孕期日记", result["summary"])

    def test_read_returns_requested_date_content_to_model_output_only(self) -> None:
        data_store.save_pregnancy_diary_entry(
            user_id="app-user",
            entry_date="2026-06-09",
            content="昨天产检排队很久，晚上有点累。",
        )

        result = manage_pregnancy_diary(
            {"action": "read", "entry_date": "2026-06-09", "start_date": None, "end_date": None, "limit": 7, "content": None, "confirmed": False},
            self._inputs(),
        )
        wrapped = {"ok": True, "tool_name": "pregnancy_diary_manage", "result": result}

        self.assertEqual(result["status"], "diary_entry_read")
        self.assertEqual(result["diary"]["content"], "昨天产检排队很久，晚上有点累。")
        self.assertNotIn("content", safe_tool_result(wrapped)["diary"])
        compact = model_tool_output(wrapped)
        self.assertEqual(compact["diary"]["content"], "昨天产检排队很久，晚上有点累。")
        self.assertIn("只根据 diary.content 回答用户", compact["final_response_instruction"])

    def test_write_creates_content_only_diary(self) -> None:
        result = manage_pregnancy_diary(
            {
                "action": "write",
                "entry_date": None,
                "start_date": None,
                "end_date": None,
                "limit": 7,
                "content": "今天胎动挺正常，晚上睡得不太好，有点腰酸。",
                "confirmed": False,
            },
            self._inputs("帮我记一下今天胎动挺正常，晚上睡得不太好，有点腰酸"),
        )

        self.assertEqual(result["status"], "diary_entry_written")
        self.assertTrue(result["side_effect_performed"])
        diary = data_store.get_pregnancy_diary_entry_by_date(user_id="app-user", entry_date="2026-06-10")
        self.assertIsNotNone(diary)
        self.assertEqual(diary["content"], "今天胎动挺正常，晚上睡得不太好，有点腰酸。")
        self.assertEqual(diary["fetal_movement"], "")
        self.assertEqual(diary["sleep_summary"], "")
        self.assertEqual(diary["symptom_tags"], [])

    def test_write_existing_entry_returns_existing_content_for_update(self) -> None:
        data_store.save_pregnancy_diary_entry(
            user_id="app-user",
            entry_date="2026-06-10",
            content="今天胎动正常，晚上睡得不太好。",
        )

        result = manage_pregnancy_diary(
            {
                "action": "write",
                "entry_date": None,
                "start_date": None,
                "end_date": None,
                "limit": 7,
                "content": "下午还有点腰酸。",
                "confirmed": False,
            },
            self._inputs("对了，下午还有点腰酸"),
        )
        compact = model_tool_output({"ok": True, "tool_name": "pregnancy_diary_manage", "result": result})

        self.assertEqual(result["status"], "entry_already_exists")
        self.assertFalse(result["side_effect_performed"])
        self.assertEqual(result["diary"]["content"], "今天胎动正常，晚上睡得不太好。")
        self.assertEqual(compact["diary"]["content"], "今天胎动正常，晚上睡得不太好。")
        self.assertIn("重新组织完整正文后调用 pregnancy_diary_manage action=update", compact["final_response_instruction"])

    def test_update_overwrites_content_and_preserves_app_structured_fields(self) -> None:
        data_store.save_pregnancy_diary_entry(
            user_id="app-user",
            entry_date="2026-06-10",
            mood="平稳",
            fetal_movement="胎动正常",
            symptom_tags=["腰酸"],
            content="今天胎动正常，晚上睡得不太好。",
        )

        result = manage_pregnancy_diary(
            {
                "action": "update",
                "entry_date": None,
                "start_date": None,
                "end_date": None,
                "limit": 7,
                "content": "今天胎动正常，晚上睡得不太好，下午有点腰酸。",
                "confirmed": False,
            },
            self._inputs("对了，下午还有点腰酸"),
        )

        self.assertEqual(result["status"], "diary_entry_updated")
        self.assertEqual(result["diary"]["content"], "今天胎动正常，晚上睡得不太好，下午有点腰酸。")
        self.assertEqual(result["diary"]["mood"], "平稳")
        self.assertEqual(result["diary"]["fetal_movement"], "胎动正常")
        self.assertEqual(result["diary"]["symptom_tags"], ["腰酸"])

    def test_delete_requires_confirmation_by_date(self) -> None:
        data_store.save_pregnancy_diary_entry(user_id="app-user", entry_date="2026-06-10", content="今天记录。")
        base_args = {
            "action": "delete",
            "entry_date": "2026-06-10",
            "start_date": None,
            "end_date": None,
            "limit": 7,
            "content": None,
        }

        needs_confirm = manage_pregnancy_diary({**base_args, "confirmed": False}, self._inputs())
        self.assertEqual(needs_confirm["status"], "needs_delete_confirmation")
        self.assertIsNotNone(data_store.get_pregnancy_diary_entry_by_date(user_id="app-user", entry_date="2026-06-10"))

        deleted = manage_pregnancy_diary({**base_args, "confirmed": True}, self._inputs())
        self.assertEqual(deleted["status"], "diary_entry_deleted")
        self.assertIsNone(data_store.get_pregnancy_diary_entry_by_date(user_id="app-user", entry_date="2026-06-10"))

    def test_legacy_health_consultation_action_is_unsupported_without_side_effect(self) -> None:
        result = manage_pregnancy_diary(
            {
                "action": "record_health_consultation",
                "entry_date": None,
                "health_topic": "胎动变化",
                "health_user_report": "晚上感觉胎动比平时少。",
                "health_suggestion_summary": "先安静观察并记录变化。",
                "confirmed": False,
            },
            self._inputs("胎动少"),
        )

        self.assertEqual(result["status"], "unsupported_action")
        self.assertFalse(result["side_effect_performed"])
        self.assertEqual(data_store.list_pregnancy_diary_entries(user_id="app-user", limit=10), [])

    def test_request_context_includes_diary_policy_even_without_entries(self) -> None:
        context = build_request_context(self._inputs("今天产检排队很久，晚上有点累"))

        self.assertIn("pregnancy_diary_context:", context)
        self.assertIn("recent_entries=0", context)
        self.assertIn("可以主动写入孕期日记", context)
        self.assertIn("写入不必另行追问确认", context)
        self.assertIn("纯科普", context)
        self.assertIn("删除仍必须 confirmed=true", context)

    def test_request_context_includes_lightweight_diary_summary_without_old_health_terms(self) -> None:
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
        data_store.add_pregnancy_diary_health_note(
            user_id="app-user",
            entry_date="2026-06-10",
            topic="腰酸和水肿",
            user_report="下午走路累，腰酸。",
        )

        context = build_request_context(self._inputs("帮我整理孕期日记"))

        self.assertIn("pregnancy_diary_context:", context)
        self.assertIn("recent_entries=1", context)
        self.assertIn("latest_date=2026-06-10", context)
        self.assertIn("latest_has_content=True", context)
        self.assertIn("pregnancy_diary_manage", context)
        self.assertNotIn("appointment_questions", context)
        self.assertNotIn("health_consultations", context)
        self.assertNotIn("latest_health_topic", context)
        self.assertNotIn("recent_tags", context)
        self.assertNotIn("今天下午走路有点累", context)

    def test_model_output_instructs_write_update_and_delete_final_replies(self) -> None:
        for status, action, expected in (
            ("diary_entry_written", "write", "孕期日记已经记录"),
            ("diary_entry_updated", "update", "孕期日记已经修改"),
            ("diary_entry_deleted", "delete", "孕期日记已经删除"),
        ):
            with self.subTest(status=status):
                compact = model_tool_output(
                    {
                        "ok": True,
                        "tool_name": "pregnancy_diary_manage",
                        "result": {
                            "ok": True,
                            "tool_name": "pregnancy_diary_manage",
                            "status": status,
                            "action": action,
                            "side_effect_performed": True,
                            "summary": "2026-06-10，今天走路有点累。",
                            "diary": {
                                "entry_id": 7,
                                "entry_date": "2026-06-10",
                                "content": "今天走路有点累。",
                            },
                        },
                    }
                )

                self.assertIn(expected, compact["final_response_instruction"])
                if action != "delete":
                    self.assertEqual(compact["diary"]["entry_id"], 7)
                    self.assertNotIn("content", compact["diary"])
                self.assertNotIn("assistant_followup", compact)

    def test_diary_write_instruction_does_not_stop_health_consultation(self) -> None:
        compact = model_tool_output(
            {
                "ok": True,
                "tool_name": "pregnancy_diary_manage",
                "result": {
                    "ok": True,
                    "tool_name": "pregnancy_diary_manage",
                    "status": "diary_entry_written",
                    "action": "write",
                    "side_effect_performed": True,
                    "summary": "2026-06-10，没有发烧，也没有红肿。",
                    "diary": {
                        "entry_id": 7,
                        "entry_date": "2026-06-10",
                        "content": "今天堵奶后确认没有发烧，也没有红肿。",
                    },
                },
            }
        )

        instruction = compact["final_response_instruction"]
        self.assertIn("不要停在记录结果", instruction)
        self.assertIn("继续完成当前健康咨询的下一步", instruction)
        self.assertIn("追问关键问题、给低风险建议", instruction)


if __name__ == "__main__":
    unittest.main()
