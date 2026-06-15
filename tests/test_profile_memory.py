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


def _birth_journey_plan_context(**overrides: object) -> dict[str, object]:
    context: dict[str, object] = {
        "due_date_or_week": "孕32周",
        "birth_path": "还没确定",
        "support_person": "暂时没有",
        "first_birth": "跳过",
        "fetus_count": "跳过",
        "age": "跳过",
        "city_or_country": "跳过",
        "checkup_status": "跳过",
        "current_symptoms": "跳过",
        "risk_factors": "跳过",
        "lifestyle_context": "跳过",
        "feeding_ibclc_context": "跳过",
    }
    context.update(overrides)
    return context


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
        data_store.update_current_care_stage(
            user_id="profile-birth-prep-reset",
            stage="pregnancy",
            source="user_intent",
        )

        cleared = data_store.reset_birth_prep_profile_memory_for_dev()

        self.assertGreaterEqual(cleared, 1)
        profile = data_store.get_user_profile("profile-birth-prep-reset")
        self.assertEqual(profile["birth_prep_due_date_or_week"], "")
        self.assertEqual(profile["birth_prep_birth_path"], "")
        self.assertEqual(profile["birth_prep_support_person"], "")
        self.assertEqual(profile["current_care_stage"], "")
        self.assertEqual(profile["current_care_stage_source"], "")

    def test_dev_startup_reset_keeps_status_demo_fields_and_clears_other_data(self) -> None:
        data_store.update_user_profile_memory(
            user_id="profile-startup-reset",
            display_name="小雨",
            age=29,
            onboarding_skipped=False,
        )
        data_store.update_birth_prep_profile_memory(
            user_id="profile-startup-reset",
            due_date_or_week="孕32周",
            birth_path="剖宫产",
            support_person="伴侣",
        )
        data_store.update_user_profile_advice(
            user_id="profile-startup-reset",
            lactation_advice="奶量偏低",
            feeding_advice="关注宝宝摄入",
        )
        data_store.save_care_plan_artifact(
            user_id="profile-startup-reset",
            plan_type="birth_journey",
            title="生产全过程计划",
            summary="孕晚期准备",
            payload={"title": "生产全过程计划"},
            source_artifact_type="birth_journey_plan_card",
        )
        data_store.save_pregnancy_diary_entry(
            user_id="profile-startup-reset",
            entry_date="2026-06-10",
            content="今天记录。",
        )
        data_store.save_uploaded_file(
            {
                "id": "file-reset",
                "name": "产检记录.pdf",
                "extension": ".pdf",
                "mime_type": "application/pdf",
                "size": 123,
                "path": "/tmp/file-reset.pdf",
                "created_at": 123456,
            }
        )
        data_store.upsert_pump_health("profile-startup-reset", 1, 0)
        with data_store._connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                """
                INSERT INTO milk_plan(user_id, plan_name, plan_type, plan_days, plan_summary)
                VALUES ('profile-startup-reset', '追奶计划', 'increase', 7, 'demo')
                """
            )

        cleared = data_store.reset_non_status_demo_data_for_dev()

        self.assertGreaterEqual(cleared["profile_onboarding"], 1)
        self.assertGreaterEqual(cleared["birth_prep_profile"], 1)
        self.assertGreaterEqual(cleared["generated_plans"], 1)
        self.assertGreaterEqual(cleared["milk_plans"], 1)
        self.assertGreaterEqual(cleared["pregnancy_diary"], 1)
        self.assertGreaterEqual(cleared["uploaded_files"], 1)
        self.assertGreaterEqual(cleared["device_runtime"], 1)
        profile = data_store.get_user_profile("profile-startup-reset")
        self.assertEqual(profile["display_name"], "")
        self.assertIsNone(profile["age"])
        self.assertEqual(profile["birth_prep_due_date_or_week"], "")
        self.assertEqual(profile["birth_prep_birth_path"], "")
        self.assertEqual(profile["birth_prep_support_person"], "")
        self.assertEqual(profile["lactation_advice"], "奶量偏低")
        self.assertEqual(profile["feeding_advice"], "关注宝宝摄入")
        self.assertEqual(data_store.list_care_plan_artifacts(user_id="profile-startup-reset"), [])
        self.assertEqual(data_store.list_pregnancy_diary_entries(user_id="profile-startup-reset"), [])
        self.assertIsNone(data_store.get_uploaded_file("file-reset"))
        self.assertIsNone(data_store.get_pump_health("profile-startup-reset"))

    def test_current_care_stage_persists_in_profile(self) -> None:
        profile = data_store.update_current_care_stage(
            user_id="profile-care-stage",
            stage="postpartum",
            source="user_intent",
        )

        self.assertIsNotNone(profile)
        saved = data_store.get_user_profile("profile-care-stage")
        self.assertEqual(saved["current_care_stage"], "postpartum")
        self.assertEqual(saved["current_care_stage_source"], "user_intent")

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
                "plan_context": _birth_journey_plan_context(
                    due_date_or_week="孕32周",
                    birth_path="顺产",
                    support_person="伴侣",
                )
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

    def test_active_birth_journey_plan_reused_by_hospital_bag_form_when_profile_memory_is_empty(self) -> None:
        create_birth_journey_plan_card(
            {
                "plan_context": _birth_journey_plan_context(
                    due_date_or_week="孕34周",
                    delivery_method="剖腹产",
                    support_person="伴侣",
                )
            },
            {
                "user_id": "profile-birth-prep-active-plan",
                "user_profile": {"user_id": "profile-birth-prep-active-plan"},
                "user_message": "",
                "locale": "zh-CN",
                "message_sent_at": "2026-06-13T10:00:00+08:00",
            },
        )
        data_store.reset_birth_prep_profile_memory_for_dev()

        form = create_hospital_bag_form(
            {"default_values": {}},
            {
                "user_id": "profile-birth-prep-active-plan",
                "user_profile": {"user_id": "profile-birth-prep-active-plan"},
                "user_message": "",
            },
        )

        defaults = form["form"]["default_values"]
        self.assertEqual(defaults["due_date_or_week"], "孕34周")
        self.assertEqual(defaults["birth_path"], "剖宫产")
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
            {
                "plan_context": _birth_journey_plan_context(
                    due_date_or_week="孕35周",
                    birth_path="剖宫产",
                    support_person="有人全天帮忙",
                    first_birth="是",
                    fetus_count="单胎",
                    feeding_intention="母乳",
                    risk_factors=["没有"],
                    lifestyle_context="怕漏买",
                    feeding_ibclc_context="母乳",
                )
            },
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
