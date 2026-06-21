from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROFILE_MEMORY_DB_PATH = os.path.join(tempfile.mkdtemp(prefix="momcozy-profile-tests-"), "profile.db")
os.environ["MILK_DB_PATH"] = PROFILE_MEMORY_DB_PATH

from momcozy_agent.contexts import ContextState, build_request_context, merge_extracted_birth_prep_slots
from momcozy_agent.server import (
    ChatRuntime,
    ChatSession,
    _hydrate_session_user_profile,
    _refresh_session_profile_cache_from_inputs,
    _runtime_inputs_from_ag_ui,
    stream_ag_ui_events,
)
from momcozy_agent.services import data_store, profile_write_queue
from momcozy_agent.tool_handlers.cards import (
    create_form,
    create_birth_journey_plan_card,
    create_hospital_bag_card,
    create_hospital_bag_form,
    manage_birth_journey_intake,
    _persist_birth_prep_profile_memory,
)
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
    def tearDown(self) -> None:
        profile_write_queue.wait_for_pending_profile_writes()

    def test_runtime_input_parsing_does_not_read_profile_db(self) -> None:
        with patch.object(data_store, "get_user_profile", side_effect=AssertionError("unexpected profile read")):
            inputs = _runtime_inputs_from_ag_ui({"message": "你好", "user_id": "profile-parse-no-db"})

        self.assertEqual(inputs["user_id"], "profile-parse-no-db")
        self.assertEqual(inputs["user_profile"], {"user_id": "profile-parse-no-db"})

    def test_session_profile_cache_avoids_repeated_entry_read(self) -> None:
        user_id = "profile-cache-user"
        data_store.update_user_profile_memory(user_id=user_id, display_name="小雨", age=29)
        session = ChatSession("profile-cache-thread")

        first_inputs = {"user_id": user_id, "user_profile": {"user_id": user_id}, "user_message": "你好"}
        _hydrate_session_user_profile(first_inputs, session)
        self.assertEqual(first_inputs["user_profile"]["display_name"], "小雨")

        second_inputs = {"user_id": user_id, "user_profile": {"user_id": user_id}, "user_message": "继续"}
        session.profile_cache_loaded_at = 0.0
        with patch.object(data_store, "get_user_profile", side_effect=AssertionError("cache miss")):
            _hydrate_session_user_profile(second_inputs, session)

        self.assertEqual(second_inputs["user_profile"]["display_name"], "小雨")

    def test_profile_tool_reuses_hydrated_profile_context(self) -> None:
        inputs = {
            "user_id": "profile-tool-cache",
            "user_profile": {"user_id": "profile-tool-cache", "display_name": "小雨", "age": 29},
            "_user_profile_loaded_from_db": True,
        }

        with patch.object(data_store, "get_user_profile", side_effect=AssertionError("unexpected profile read")):
            profile = get_profile({}, inputs)["user_profile"]

        self.assertEqual(profile["display_name"], "小雨")
        self.assertEqual(profile["age"], 29)

    def test_profile_update_refreshes_runtime_profile_and_session_cache(self) -> None:
        user_id = "profile-cache-update"
        session = ChatSession("profile-cache-update-thread")
        inputs = {"user_id": user_id, "user_profile": {"user_id": user_id}, "user_message": "我叫小雨"}
        _hydrate_session_user_profile(inputs, session)

        update_profile({"display_name": "小雨", "age": 29, "onboarding_skipped": False}, inputs)
        _refresh_session_profile_cache_from_inputs(session, inputs)

        next_inputs = {"user_id": user_id, "user_profile": {"user_id": user_id}, "user_message": "继续"}
        with patch.object(data_store, "get_user_profile", side_effect=AssertionError("cache miss after update")):
            _hydrate_session_user_profile(next_inputs, session)

        self.assertEqual(next_inputs["user_profile"]["display_name"], "小雨")
        self.assertEqual(next_inputs["user_profile"]["age"], 29)

    def test_stream_error_still_refreshes_runtime_profile_cache(self) -> None:
        user_id = "profile-error-cache"
        runtime = ChatRuntime(object(), slot_extractor=None)
        payload = {"thread_id": "profile-error-cache-thread", "run_id": "profile-error-cache-run"}
        inputs = {"user_id": user_id, "user_profile": {"user_id": user_id}, "user_message": "我叫小雨"}

        def failing_agent_loop(client: object, runtime_inputs: dict[str, object], *args: object, **kwargs: object) -> object:
            runtime_inputs["user_profile"] = {
                **runtime_inputs.get("user_profile", {}),
                "display_name": "小雨",
                "age": 29,
            }
            runtime_inputs["_user_profile_loaded_from_db"] = True
            raise RuntimeError("model failed after profile update")

        async def collect_events() -> list[dict[str, object]]:
            return [event async for event in stream_ag_ui_events(payload, inputs, runtime)]

        with patch("momcozy_agent.server.run_agent_loop", side_effect=failing_agent_loop):
            events = asyncio.run(collect_events())

        self.assertTrue(any(event.get("type") == "RUN_ERROR" for event in events))
        session = runtime.get_session("profile-error-cache-thread", user_id=user_id)
        next_inputs = {"user_id": user_id, "user_profile": {"user_id": user_id}, "user_message": "继续"}
        with patch.object(data_store, "get_user_profile", side_effect=AssertionError("cache miss after error")):
            _hydrate_session_user_profile(next_inputs, session)

        self.assertEqual(next_inputs["user_profile"]["display_name"], "小雨")
        self.assertEqual(next_inputs["user_profile"]["age"], 29)

    def test_profile_update_queues_db_write_and_updates_runtime_without_sync_write(self) -> None:
        inputs = {
            "user_id": "profile-async-update",
            "user_profile": {"user_id": "profile-async-update"},
            "_user_profile_loaded_from_db": True,
        }

        with patch.object(data_store, "update_user_profile_memory", side_effect=AssertionError("sync profile write")):
            with patch.object(profile_write_queue, "enqueue_user_profile_update", return_value=True) as enqueue:
                result = update_profile({"display_name": "小雨", "age": 29, "onboarding_skipped": False}, inputs)

        self.assertEqual(result["status"], "profile_updated")
        self.assertEqual(inputs["user_profile"]["display_name"], "小雨")
        self.assertEqual(inputs["user_profile"]["age"], 29)
        enqueue.assert_called_once()

    def test_birth_prep_profile_memory_queues_db_write_and_updates_runtime_without_sync_write(self) -> None:
        inputs = {
            "user_id": "profile-birth-prep-async",
            "user_profile": {"user_id": "profile-birth-prep-async"},
        }

        with patch.object(data_store, "update_birth_prep_profile_memory", side_effect=AssertionError("sync birth prep write")):
            with patch.object(profile_write_queue, "enqueue_birth_prep_profile_update", return_value=True) as enqueue:
                _persist_birth_prep_profile_memory(
                    inputs,
                    {"due_date_or_week": "孕32周", "city_or_country": "深圳", "birth_path": "剖宫产"},
                )

        self.assertEqual(inputs["user_profile"]["birth_prep_due_date_or_week"], "孕32周")
        self.assertEqual(inputs["user_profile"]["birth_prep_city_or_country"], "深圳")
        self.assertEqual(inputs["user_profile"]["birth_prep_birth_path"], "剖宫产")
        enqueue.assert_called_once()

    def test_profile_write_retry_preserves_enqueue_order(self) -> None:
        self.assertTrue(profile_write_queue.wait_for_pending_profile_writes())
        calls: list[str] = []

        def flaky_update_user_profile_memory(**kwargs: object) -> dict[str, object]:
            display_name = str(kwargs.get("display_name") or "")
            calls.append(display_name)
            if display_name == "旧名字" and calls.count("旧名字") == 1:
                raise RuntimeError("temporary db lock")
            return {"user_id": kwargs.get("user_id"), "display_name": display_name}

        with patch.object(data_store, "update_user_profile_memory", side_effect=flaky_update_user_profile_memory):
            profile_write_queue.enqueue_user_profile_update(user_id="profile-write-order", display_name="旧名字")
            profile_write_queue.enqueue_user_profile_update(user_id="profile-write-order", display_name="新名字")
            self.assertTrue(profile_write_queue.wait_for_pending_profile_writes())

        self.assertEqual(calls, ["旧名字", "旧名字", "新名字"])

    def test_init_db_skips_schema_work_after_same_path_initialized(self) -> None:
        old_db_path = data_store.DB_PATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                data_store.DB_PATH = Path(tmp) / "profile-init-cache.db"  # type: ignore[assignment]
                data_store.init_db(force=True)
                with patch.object(data_store, "_ensure_column", side_effect=AssertionError("schema check repeated")):
                    data_store.init_db()
        finally:
            data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_profile_update_persists_name_and_age(self) -> None:
        result = update_profile(
            {"display_name": "小雨", "age": 29, "onboarding_skipped": False},
            {"user_id": "profile-user-1", "locale": "zh-CN", "user_message": "我叫小雨，今年29岁"},
        )

        self.assertEqual(result["status"], "profile_updated")
        self.assertTrue(result["profile_onboarding_complete"])
        self.assertTrue(profile_write_queue.wait_for_pending_profile_writes())
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
        self.assertTrue(profile_write_queue.wait_for_pending_profile_writes())
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
        self.assertTrue(profile_write_queue.wait_for_pending_profile_writes())

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
            age=32,
            due_date_or_week="孕32周",
            ivf="否",
            fetus_count="单胎",
            city_or_country="深圳",
            birth_hospital="深圳市妇幼",
            birth_path="剖宫产",
            first_birth="是",
            feeding_intention="母乳",
            return_to_work_timing="6 周后",
            support_person="伴侣",
            pregnancy_history_or_notes=["没有"],
            top_worries=["怕漏买"],
        )
        cleared = data_store.reset_birth_prep_profile_memory_for_dev()

        self.assertGreaterEqual(cleared, 1)
        profile = data_store.get_user_profile("profile-birth-prep-reset")
        self.assertEqual(profile["birth_prep_due_date_or_week"], "")
        self.assertEqual(profile["birth_prep_ivf"], "")
        self.assertEqual(profile["birth_prep_fetus_count"], "")
        self.assertEqual(profile["birth_prep_city_or_country"], "")
        self.assertEqual(profile["birth_prep_birth_hospital"], "")
        self.assertEqual(profile["birth_prep_birth_path"], "")
        self.assertEqual(profile["birth_prep_first_birth"], "")
        self.assertEqual(profile["birth_prep_feeding_intention"], "")
        self.assertEqual(profile["birth_prep_return_to_work_timing"], "")
        self.assertEqual(profile["birth_prep_support_person"], "")
        self.assertEqual(profile["birth_prep_pregnancy_history_or_notes"], "")
        self.assertEqual(profile["birth_prep_top_worries"], "")

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
            title="孕期计划",
            summary="孕晚期准备",
            payload={"title": "孕期计划"},
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

    def test_birth_prep_user_text_slots_do_not_persist_profile_memory(self) -> None:
        state = ContextState()
        inputs = {
            "user_id": "profile-birth-prep-1",
            "locale": "zh-CN",
            "timezone": "Asia/Shanghai",
            "message_sent_at": "2026-06-13T10:00:00+08:00",
            "user_message": "我今年32岁，现在孕30周，试管，双胎，在深圳，建档医院是深圳市妇幼，计划剖宫产，老公陪我。",
        }

        merge_extracted_birth_prep_slots(
            state,
            [
                {"field_id": "age", "value": 32, "evidence": "我今年32岁", "confidence": 0.9},
                {"field_id": "due_date_or_week", "value": "孕30周", "evidence": "孕30周", "confidence": 0.9},
                {"field_id": "ivf", "value": "是", "evidence": "试管", "confidence": 0.9},
                {"field_id": "fetus_count", "value": "双胎", "evidence": "双胎", "confidence": 0.9},
                {"field_id": "city_or_country", "value": "深圳", "evidence": "在深圳", "confidence": 0.9},
                {"field_id": "birth_hospital", "value": "深圳市妇幼", "evidence": "建档医院是深圳市妇幼", "confidence": 0.9},
                {"field_id": "birth_path", "value": "剖宫产", "evidence": "计划剖宫产", "confidence": 0.9},
                {"field_id": "support_person", "value": "有人全天帮忙", "evidence": "老公陪我", "confidence": 0.9},
            ],
            turn_id=1,
            updated_at=inputs["message_sent_at"],
        )

        profile = data_store.get_user_profile("profile-birth-prep-1")
        self.assertEqual(profile["birth_prep_due_date_or_week"], "")
        self.assertEqual(profile["birth_prep_ivf"], "")
        self.assertEqual(profile["birth_prep_fetus_count"], "")
        self.assertEqual(profile["birth_prep_city_or_country"], "")
        self.assertEqual(profile["birth_prep_birth_hospital"], "")
        self.assertEqual(profile["birth_prep_birth_path"], "")
        self.assertEqual(profile["birth_prep_support_person"], "")
        slot_record = state.birth_prep_slots["hospital_bag"]["due_date_or_week"]
        self.assertEqual(slot_record["value"], "孕30周")
        self.assertEqual(slot_record["status"], "confirmed")

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
        self.assertTrue(profile_write_queue.wait_for_pending_profile_writes())
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
        self.assertTrue(profile_write_queue.wait_for_pending_profile_writes())
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
        self.assertTrue(profile_write_queue.wait_for_pending_profile_writes())
        profile = data_store.get_user_profile("profile-birth-prep-3")
        self.assertEqual(profile["birth_prep_fetus_count"], "单胎")
        self.assertEqual(profile["birth_prep_first_birth"], "是")
        self.assertEqual(profile["birth_prep_feeding_intention"], "亲喂母乳")
        self.assertEqual(profile["birth_prep_return_to_work_timing"], "6 周后")
        self.assertEqual(profile["birth_prep_top_worries"], "怕漏买")

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

    def test_birth_journey_basic_info_prefills_hospital_bag_form(self) -> None:
        user_id = "profile-birth-prep-basic-info"
        manage_birth_journey_intake(
            {
                "action": "submit_basic_info",
                "payload": {
                    "current_week": "25",
                    "age": 29,
                    "fetus_count": "单胎",
                    "city_or_country": "深圳",
                },
            },
            {
                "user_id": user_id,
                "user_profile": {"user_id": user_id},
                "user_message": "",
            },
        )
        self.assertTrue(profile_write_queue.wait_for_pending_profile_writes())
        profile = data_store.get_user_profile(user_id)
        form = create_hospital_bag_form(
            {"default_values": {"feeding_intention": "母乳"}},
            {
                "user_id": user_id,
                "user_profile": profile,
                "user_message": "",
            },
        )

        defaults = form["form"]["default_values"]
        self.assertEqual(defaults["due_date_or_week"], "25周")
        self.assertEqual(defaults["fetus_count"], "单胎")
        self.assertEqual(defaults["feeding_intention"], "亲喂母乳")
        default_by_id = {field["id"]: field.get("default_value") for field in form["form"]["fields"]}
        self.assertEqual(default_by_id["due_date_or_week"], "25周")
        self.assertEqual(default_by_id["fetus_count"], "单胎")

        generic_form = create_form(
            {
                "form_id": "custom_hospital_bag_form",
                "title": "信息采集",
                "fields": [
                    {"id": "due_date_or_week", "label": "预产期或当前孕周", "type": "text"},
                    {"id": "fetus_count", "label": "胎数", "type": "select", "options": ["单胎", "双胎", "三胎及以上", "不确定"]},
                    {"id": "feeding_intention", "label": "喂养意向", "type": "select", "options": ["亲喂母乳", "配方奶", "混合喂养", "还不确定"]},
                ],
            },
            {
                "user_id": user_id,
                "user_profile": profile,
                "user_message": "",
            },
        )
        generic_defaults = generic_form["form"]["default_values"]
        self.assertEqual(generic_defaults["due_date_or_week"], "25周")
        self.assertEqual(generic_defaults["fetus_count"], "单胎")

    def test_profile_update_is_available_but_not_read_only(self) -> None:
        tool_names = [str(tool.get("name")) for tool in select_runtime_tools() if tool.get("type") == "function"]

        self.assertIn("profile_update", tool_names)
        self.assertNotIn("profile_update", READ_ONLY_TOOL_NAMES)


if __name__ == "__main__":
    unittest.main()
