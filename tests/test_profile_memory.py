from __future__ import annotations

import json
import os
import tempfile
import unittest


PROFILE_MEMORY_DB_PATH = os.path.join(tempfile.mkdtemp(prefix="momcozy-profile-tests-"), "profile.db")
os.environ["MILK_DB_PATH"] = PROFILE_MEMORY_DB_PATH

from momcozy_agent.contexts import ContextState, build_request_context
from momcozy_agent.services import data_store
from momcozy_agent.tool_handlers.cards import create_birth_journey_plan_card, create_hospital_bag_card, create_hospital_bag_form
from momcozy_agent.tool_handlers.profile import get_profile, update_profile
from momcozy_agent.tool_registry import READ_ONLY_TOOL_NAMES, select_runtime_tools


class ProfileMemoryTests(unittest.TestCase):
    def test_profile_update_persists_name_and_age(self) -> None:
        result = update_profile(
            {"display_name": "小雨", "age": 29, "onboarding_skipped": False},
            {"user_id": "profile-user-1", "locale": "zh-CN", "user_message": "我叫小雨，今年29岁"},
        )

        self.assertEqual(result["status"], "profile_updated")
        self.assertTrue(result["profile_onboarding_complete"])
        profile = data_store.get_user_profile("profile-user-1")
        self.assertEqual(profile["display_name"], "小雨")
        self.assertEqual(profile["age"], 29)
        self.assertTrue(profile["profile_onboarding_complete"])
        self.assertFalse(profile["profile_onboarding_skipped"])

    def test_profile_update_remembers_onboarding_skip(self) -> None:
        result = update_profile(
            {"display_name": None, "age": None, "onboarding_skipped": True},
            {"user_id": "profile-user-2", "locale": "zh-CN", "user_message": "先跳过吧"},
        )

        self.assertEqual(result["status"], "profile_updated")
        self.assertFalse(result["profile_onboarding_complete"])
        self.assertTrue(result["profile_onboarding_skipped"])
        profile = get_profile({}, {"user_id": "profile-user-2", "locale": "zh-CN", "user_message": ""})["user_profile"]
        self.assertTrue(profile["profile_onboarding_skipped"])

    def test_dev_startup_reset_clears_profile_answers_and_skip_gate(self) -> None:
        update_profile(
            {"display_name": "小雨", "age": 29, "onboarding_skipped": False},
            {"user_id": "profile-user-reset", "locale": "zh-CN", "user_message": "我叫小雨，今年29岁"},
        )
        update_profile(
            {"display_name": None, "age": None, "onboarding_skipped": True},
            {"user_id": "profile-user-skipped", "locale": "zh-CN", "user_message": "先跳过"},
        )

        cleared = data_store.reset_profile_onboarding_memory_for_dev()

        self.assertGreaterEqual(cleared, 2)
        remembered_profile = data_store.get_user_profile("profile-user-reset")
        skipped_profile = data_store.get_user_profile("profile-user-skipped")
        self.assertEqual(remembered_profile["display_name"], "")
        self.assertIsNone(remembered_profile["age"])
        self.assertFalse(remembered_profile["profile_onboarding_complete"])
        self.assertFalse(remembered_profile["profile_onboarding_skipped"])
        self.assertFalse(skipped_profile["profile_onboarding_skipped"])

    def test_dev_startup_reset_clears_birth_prep_shared_memory(self) -> None:
        data_store.update_birth_prep_profile_memory(
            user_id="profile-birth-prep-reset",
            due_date_or_week="孕32周",
            birth_path="剖宫产",
            support_person="伴侣",
        )

        cleared = data_store.reset_birth_prep_profile_memory_for_dev()

        self.assertGreaterEqual(cleared, 1)
        profile = data_store.get_user_profile("profile-birth-prep-reset")
        self.assertEqual(profile["birth_prep_due_date_or_week"], "")
        self.assertEqual(profile["birth_prep_birth_path"], "")
        self.assertEqual(profile["birth_prep_support_person"], "")

    def test_user_profile_context_is_only_injected_at_session_start(self) -> None:
        state = ContextState()
        inputs = {
            "user_id": "profile-user-3",
            "locale": "zh-CN",
            "timezone": "Asia/Shanghai",
            "message_sent_at": "2026-06-13T10:00:00+08:00",
            "user_message": "你好",
            "user_profile": {
                "user_id": "profile-user-3",
                "display_name": "小雨",
                "age": 29,
                "birth_prep_due_date_or_week": "孕30周",
                "birth_prep_birth_path": "顺产",
                "birth_prep_support_person": "伴侣",
            },
        }

        first = build_request_context(inputs, state)
        second = build_request_context({**inputs, "user_message": "继续"}, state)

        self.assertIn("user_profile_context:", first)
        self.assertIn("display_name=小雨", first)
        self.assertIn("age=29", first)
        self.assertNotIn("birth_prep_due_date_or_week=孕30周", first)
        self.assertNotIn("user_profile_context:", second)
        self.assertNotIn("display_name=小雨", second)
        self.assertIn("birth_prep_profile_context:", first)
        self.assertIn("due_date_or_week=孕30周", first)
        self.assertIn("birth_path=顺产", first)
        self.assertIn("support_person=伴侣", first)
        self.assertIn("birth_prep_profile_context:", second)
        self.assertIn("due_date_or_week=孕30周", second)

    def test_birth_prep_shared_fields_persist_from_natural_message(self) -> None:
        state = ContextState()
        inputs = {
            "user_id": "profile-birth-prep-1",
            "locale": "zh-CN",
            "timezone": "Asia/Shanghai",
            "message_sent_at": "2026-06-13T10:00:00+08:00",
            "user_message": "我现在孕30周，计划剖宫产，老公陪我。",
        }

        from momcozy_agent.contexts import capture_birth_prep_user_message

        capture_birth_prep_user_message(inputs, state)

        profile = data_store.get_user_profile("profile-birth-prep-1")
        self.assertEqual(profile["birth_prep_due_date_or_week"], "孕30周")
        self.assertEqual(profile["birth_prep_birth_path"], "剖宫产")
        self.assertEqual(profile["birth_prep_support_person"], "有人全天帮忙")

    def test_birth_journey_plan_memory_reused_by_hospital_bag_form(self) -> None:
        create_birth_journey_plan_card(
            {
                "plan_context": {
                    "due_date_or_week": "孕32周",
                    "birth_path": "顺产",
                    "support_person": "伴侣",
                }
            },
            {
                "user_id": "profile-birth-prep-2",
                "user_profile": {"user_id": "profile-birth-prep-2"},
                "user_message": "",
                "locale": "zh-CN",
                "message_sent_at": "2026-06-13T10:00:00+08:00",
            },
        )
        profile = data_store.get_user_profile("profile-birth-prep-2")

        form = create_hospital_bag_form(
            {"default_values": {}},
            {
                "user_id": "profile-birth-prep-2",
                "user_profile": profile,
                "user_message": "",
            },
        )

        defaults = form["form"]["default_values"]
        self.assertEqual(defaults["due_date_or_week"], "孕32周")
        self.assertEqual(defaults["birth_path"], "顺产")
        self.assertEqual(defaults["support_person"], "有人全天帮忙")

    def test_hospital_bag_memory_reused_by_birth_journey_plan(self) -> None:
        form_data = {
            "due_date_or_week": "孕35周",
            "first_birth": "是",
            "fetus_count": "单胎",
            "pregnancy_history_or_notes": ["没有"],
            "birth_path": "剖宫产",
            "feeding_intention": "母乳",
            "return_to_work_timing": "6 周后",
            "support_person": "有人全天帮忙",
            "top_worries": ["怕漏买"],
        }
        create_hospital_bag_card(
            {"confirmed_form_data": {}},
            {
                "user_id": "profile-birth-prep-3",
                "user_profile": {"user_id": "profile-birth-prep-3"},
                "user_message": (
                    "我已确认待产包信息。\n"
                    "form_id: hospital_bag_intake\n"
                    "confirmed_form_data:\n"
                    f"{json.dumps(form_data, ensure_ascii=False)}"
                ),
            },
        )
        profile = data_store.get_user_profile("profile-birth-prep-3")

        result = create_birth_journey_plan_card(
            {"plan_context": {}},
            {
                "user_id": "profile-birth-prep-3",
                "user_profile": profile,
                "user_message": "",
                "locale": "zh-CN",
                "message_sent_at": "2026-06-13T10:00:00+08:00",
            },
        )

        self.assertEqual(result["status"], "card_created")
        owner = result["card"]["card_json"]["owner"]
        self.assertEqual(owner["due_date_or_week"], "孕35周")
        self.assertEqual(owner["birth_path"], "剖宫产")
        self.assertEqual(owner["support_person"], "有人全天帮忙")

    def test_profile_update_is_available_but_not_read_only(self) -> None:
        tool_names = [str(tool.get("name")) for tool in select_runtime_tools() if tool.get("type") == "function"]

        self.assertIn("profile_update", tool_names)
        self.assertNotIn("profile_update", READ_ONLY_TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()
