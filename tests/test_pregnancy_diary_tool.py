from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from momcozy_agent.agents import model_tool_output
from momcozy_agent.contexts import build_request_context
from momcozy_agent.services import data_store
from momcozy_agent.tool_handlers.pregnancy_diary import manage_pregnancy_diary


class PregnancyDiaryToolTests(unittest.TestCase):
    def test_list_reads_existing_entries_without_seeding_demo_data(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                for offset, content in enumerate(["今天有点累。", "昨天睡得一般。"]):
                    data_store.save_pregnancy_diary_entry(
                        user_id="app-user",
                        entry_date=f"2026-06-{10 - offset:02d}",
                        content=content,
                    )
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
                self.assertEqual(len(result["diary_list"]), 2)
                entries = data_store.list_pregnancy_diary_entries(user_id="app-user", limit=10)
                self.assertEqual(len(entries), 2)
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
                self.assertEqual(data_store.list_pregnancy_diary_entries(user_id="app-user", limit=10), [])
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_record_health_consultation_appends_to_today_without_overwriting_diary(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                saved = data_store.save_pregnancy_diary_entry(
                    user_id="app-user",
                    entry_date="2026-06-10",
                    mood="平稳",
                    content="这是我自己写的日记。",
                )
                self.assertIsNotNone(saved)

                result = manage_pregnancy_diary(
                    {
                        "action": "record_health_consultation",
                        "entry_date": None,
                        "health_topic": "乳房硬块疼痛",
                        "health_user_report": "左侧有硬块，按压会疼。",
                        "health_asked_questions": ["有没有发烧或寒战？", "硬块有没有变大？"],
                        "health_known_answers": ["没有发烧", "硬块没有变大"],
                        "health_suggestion_summary": "先正常喂/吸，不要硬揉，可以冷敷观察。",
                        "health_follow_up": "晚点看疼痛和硬块大小有没有变化。",
                        "confirmed": False,
                    },
                    {"user_id": "app-user", "message_sent_at": "2026-06-10T09:00:00+08:00", "user_message": "乳房有硬块疼"},
                )

                self.assertEqual(result["status"], "health_consultation_recorded")
                self.assertTrue(result["side_effect_performed"])
                diary = data_store.get_pregnancy_diary_entry_by_date(user_id="app-user", entry_date="2026-06-10")
                self.assertIsNotNone(diary)
                self.assertEqual(diary["content"], "这是我自己写的日记。")
                self.assertEqual(diary["health_notes"][0]["topic"], "乳房硬块疼痛")
                self.assertEqual(diary["health_notes"][0]["known_answers"], ["没有发烧", "硬块没有变大"])
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_record_health_consultation_creates_today_diary_if_missing(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                result = manage_pregnancy_diary(
                    {
                        "action": "record_health_consultation",
                        "entry_date": None,
                        "health_topic": "胎动变化",
                        "health_user_report": "晚上感觉胎动比平时少。",
                        "health_asked_questions": [],
                        "health_known_answers": ["还没有其他不舒服"],
                        "health_suggestion_summary": "先安静观察并记录变化。",
                        "health_follow_up": "如果明显减少或持续不放心，再继续告诉我。",
                        "confirmed": False,
                    },
                    {"user_id": "app-user", "message_sent_at": "2026-06-10T09:00:00+08:00", "user_message": "胎动少"},
                )

                self.assertEqual(result["status"], "health_consultation_recorded")
                diary = data_store.get_pregnancy_diary_entry_by_date(user_id="app-user", entry_date="2026-06-10")
                self.assertIsNotNone(diary)
                self.assertEqual(diary["content"], "")
                self.assertEqual(diary["health_notes"][0]["topic"], "胎动变化")
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_record_health_consultation_updates_same_topic_today_note(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                base_inputs = {
                    "user_id": "app-user",
                    "message_sent_at": "2026-06-10T09:00:00+08:00",
                    "user_message": "胎动明显",
                }
                first = manage_pregnancy_diary(
                    {
                        "action": "record_health_consultation",
                        "entry_date": None,
                        "health_topic": "胎动明显",
                        "health_user_report": "用户补充目前孕25周；今天有明显胎动。",
                        "health_asked_questions": ["胎动是否和平时规律相近？"],
                        "health_known_answers": ["今天胎动明显"],
                        "health_suggestion_summary": "继续确认胎动是否与平时规律相近。",
                        "health_follow_up": "请用户补充孕周，以及胎动和以往相比是否有明显变化。",
                        "confirmed": False,
                    },
                    base_inputs,
                )
                second = manage_pregnancy_diary(
                    {
                        "action": "record_health_consultation",
                        "entry_date": None,
                        "health_topic": "胎动明显",
                        "health_user_report": "用户说今天有明显胎动，询问是什么情况。",
                        "health_asked_questions": ["是否伴随腹痛、出血或破水？"],
                        "health_known_answers": ["孕25周", "今天有明显胎动"],
                        "health_suggestion_summary": "把这次胎动变化放在一起观察。",
                        "health_follow_up": "继续确认胎动是否与平时规律相近，是否伴随腹痛、出血或破水。",
                        "confirmed": False,
                    },
                    base_inputs,
                )

                self.assertEqual(first["status"], "health_consultation_recorded")
                self.assertEqual(second["status"], "health_consultation_updated")
                diary = data_store.get_pregnancy_diary_entry_by_date(user_id="app-user", entry_date="2026-06-10")
                self.assertIsNotNone(diary)
                self.assertEqual(len(diary["health_notes"]), 1)
                note = diary["health_notes"][0]
                self.assertEqual(note["topic"], "胎动明显")
                self.assertIn("目前孕25周", note["user_report"])
                self.assertIn("询问是什么情况", note["user_report"])
                self.assertEqual(note["known_answers"], ["今天胎动明显", "孕25周", "今天有明显胎动"])
                self.assertIn("胎动是否与平时规律相近", note["follow_up"])
                self.assertIn("是否伴随腹痛", note["follow_up"])
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

    def test_dev_startup_reset_clears_pregnancy_diary_and_health_notes(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                data_store.save_pregnancy_diary_entry(
                    user_id="app-user",
                    entry_date="2026-06-10",
                    content="今天记录。",
                )
                data_store.add_pregnancy_diary_health_note(
                    user_id="app-user",
                    entry_date="2026-06-10",
                    topic="胎动明显",
                    user_report="今天有明显胎动。",
                )

                cleared = data_store.reset_pregnancy_diary_for_dev()

                self.assertEqual(cleared, 2)
                self.assertEqual(data_store.list_pregnancy_diary_entries(user_id="app-user", limit=10), [])
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
                data_store.add_pregnancy_diary_health_note(
                    user_id="app-user",
                    entry_date="2026-06-10",
                    topic="腰酸和水肿",
                    user_report="下午走路累，腰酸。",
                    suggestion_summary="先记录变化。",
                    follow_up="晚点看水肿有没有加重。",
                )

                context = build_request_context(
                    {"user_id": "app-user", "message_sent_at": "2026-06-10T09:00:00+08:00", "user_message": "帮我整理孕期日记"}
                )

                self.assertIn("pregnancy_diary_context:", context)
                self.assertIn("appointment_questions=1", context)
                self.assertIn("health_consultations=1", context)
                self.assertIn("latest_health_topic=腰酸和水肿", context)
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

    def test_model_output_instructs_health_consultation_record_final_reply(self) -> None:
        for status in ("health_consultation_recorded", "health_consultation_updated"):
            compact = model_tool_output(
                {
                    "ok": True,
                    "tool_name": "pregnancy_diary_manage",
                    "result": {
                        "ok": True,
                        "tool_name": "pregnancy_diary_manage",
                        "status": status,
                        "action": "record_health_consultation",
                        "side_effect_performed": True,
                        "summary": "2026-06-10，乳房硬块疼痛",
                        "diary": {
                            "entry_id": 9,
                            "entry_date": "2026-06-10",
                            "health_notes": [{"topic": "乳房硬块疼痛"}],
                        },
                    },
                }
            )

            self.assertIn("final_response_instruction", compact)
            self.assertIn("预问诊记录写入今天的孕期日记", compact["final_response_instruction"])
            self.assertIn("接着这次记录继续看", compact["final_response_instruction"])
            self.assertEqual(compact["diary"]["entry_id"], 9)


if __name__ == "__main__":
    unittest.main()
