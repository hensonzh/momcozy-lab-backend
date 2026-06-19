from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from momcozy_agent.agents import model_tool_output
from momcozy_agent.contexts import build_request_context
from momcozy_agent.services import data_store
from momcozy_agent.tool_handlers.cards import (
    BIRTH_JOURNEY_PLAN_ITEM_REASON_MAX_CHARS,
    BIRTH_JOURNEY_PLAN_ITEM_TITLE_MAX_CHARS,
    create_birth_journey_plan_card,
    delete_birth_journey_plan,
    manage_birth_journey_intake,
    update_birth_journey_plan_todo,
    update_birth_journey_plan_todo_completion_for_user,
)
from momcozy_agent.tool_registry import select_runtime_tools


def _plan_context(**overrides: object) -> dict[str, object]:
    context: dict[str, object] = {
        "due_date_or_week": "30周",
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


def _assert_birth_journey_item_text_lengths(testcase: unittest.TestCase, layers: dict[str, object]) -> None:
    for section_id in ("current_week_focus", "next_7_days", "next_2_4_weeks", "later_milestones"):
        section = layers.get(section_id)
        items = section.get("items") if isinstance(section, dict) else []
        testcase.assertIsInstance(items, list)
        for item in items:
            testcase.assertIsInstance(item, dict)
            title = str(item.get("title") or "")
            reason = str(item.get("reason") or "")
            testcase.assertLessEqual(len(title), BIRTH_JOURNEY_PLAN_ITEM_TITLE_MAX_CHARS)
            testcase.assertLessEqual(len(reason), BIRTH_JOURNEY_PLAN_ITEM_REASON_MAX_CHARS)


def _assert_next_7_todo_items(testcase: unittest.TestCase, layers: dict[str, object]) -> None:
    next_7 = layers.get("next_7_days")
    items = next_7.get("items") if isinstance(next_7, dict) else []
    testcase.assertIsInstance(items, list)
    testcase.assertTrue(items)
    for index, item in enumerate(items):
        testcase.assertIsInstance(item, dict)
        testcase.assertEqual(item.get("id"), f"next7_{index + 1:02d}")
        testcase.assertIs(item.get("completed"), False)
        testcase.assertIsNone(item.get("completed_at"))
        testcase.assertIsNone(item.get("completed_source"))


class BirthJourneyPlanCardTests(unittest.TestCase):
    def test_creates_early_pregnancy_phase_for_newly_pregnant_users(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="5周",
                    birth_path="还没确定",
                    support_person="伴侣",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-02T09:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        card = result["card"]["card_json"]
        self.assertEqual(card["owner"]["current_week"], "孕5周")
        phases = card["phases"]
        self.assertEqual([phase["title"] for phase in phases], ["孕早期", "孕中期", "孕晚期", "临产阶段", "住院分娩", "产后恢复"])
        self.assertEqual(phases[0]["status"], "current")
        self.assertIn("约 2026/06/02", phases[0]["date_range"])
        self.assertIn("首次产检", phases[0]["goal"])
        self.assertEqual(phases[-1]["title"], "产后恢复")

    def test_creates_structured_birth_journey_plan_card_from_week_context(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="25周",
                    first_birth="是",
                    birth_path="剖宫产",
                    feeding_intention="母乳",
                    feeding_ibclc_context="计划母乳，想了解产后支持",
                    checkup_status="已做 NT、NIPT、大排畸，结果正常，还没预约糖耐",
                    current_symptoms="最近久坐上班，腰酸，晚上腿抽筋",
                    risk_factors="孕前 BMI 28",
                    lifestyle_context="久坐上班，晚上腿抽筋",
                    support_person="伴侣",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-05-31T09:00:00+08:00"},
        )

        self.assertEqual(result["tool_name"], "birth_journey_plan_card_create")
        self.assertEqual(result["card"]["card_type"], "birth_journey_plan_card")
        self.assertEqual(result["card"]["schema_version"], "1.0")
        card = result["card"]["card_json"]
        self.assertEqual(card["title"], "孕期计划")
        self.assertEqual(card["owner"]["current_week"], "孕25周")
        self.assertEqual(card["owner"]["estimated_due_date"], "2026/09/13")
        layers = card["planning_layers"]
        self.assertEqual(layers["current_week"], 25)
        self.assertEqual(layers["current_week_focus"]["title"], "当前优先级")
        self.assertIn("最影响后续准备和安全感", layers["current_week_focus"]["subtitle"])
        self.assertTrue(layers["current_week_focus"]["items"])
        self.assertEqual(layers["next_7_days"]["title"], "接下来 7 天行动清单")
        self.assertIn("这周能完成的几个小动作", layers["next_7_days"]["subtitle"])
        self.assertTrue(layers["next_7_days"]["items"])
        _assert_next_7_todo_items(self, layers)
        self.assertTrue(layers["next_2_4_weeks"]["items"])
        self.assertTrue(layers["later_milestones"]["items"])
        _assert_birth_journey_item_text_lengths(self, layers)

        phases = card["phases"]
        self.assertEqual([phase["title"] for phase in phases], ["孕中期", "孕晚期", "临产阶段", "住院分娩", "产后恢复"])
        self.assertEqual(sum(phase["status"] == "current" for phase in phases), 1)
        self.assertIn("约 2026/05/31", phases[0]["date_range"])
        self.assertTrue(all(phase.get("goal") and phase.get("watchouts") and phase.get("actions") for phase in phases))
        self.assertEqual(
                {phase["title"]: phase.get("comate_help") for phase in phases},
                {
                    "孕中期": [],
                    "孕晚期": ["制定个性化待产清单"],
                    "临产阶段": [],
                    "住院分娩": [],
                    "产后恢复": [],
                },
            )
        self.assertEqual(card["next_action"], {"label": "整理产检问题", "send_text": "帮我整理下次产检要问的 3-5 个问题"})
        self.assertNotIn("assistant_followup", result)

        rendered = json.dumps(card, ensure_ascii=False)
        self.assertIn("剖宫产", rendered)
        self.assertIn("母乳", rendered)
        self.assertNotIn("产检资料", rendered)
        self.assertNotIn("生成产检问题清单", rendered)
        self.assertNotIn("打开分娩沟通单", rendered)
        self.assertNotIn("生成夜间分工卡", rendered)
        self.assertNotIn("承接奶量管理计划", rendered)
        self.assertNotIn("| --- |", rendered)
        self.assertNotIn("<br>", rendered)

    def test_birth_journey_plan_merges_safety_gate_into_current_focus(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="30周",
                    current_symptoms="今天有阴道流血和腹痛",
                    checkup_status="下周复查",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        layers = result["card"]["card_json"]["planning_layers"]
        safety_items = layers["safety_gate"]["items"]
        focus_items = layers["current_week_focus"]["items"]
        self.assertTrue(safety_items)
        self.assertEqual(focus_items[0]["title"], "先确认是否需要联系医院或医生")
        self.assertIn("医院口径", focus_items[0]["reason"])
        _assert_birth_journey_item_text_lengths(self, layers)

    def test_birth_journey_plan_filters_later_milestones_by_current_week(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="35周",
                    birth_hospital="深圳市妇幼",
                    feeding_intention="混合",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        milestones = result["card"]["card_json"]["planning_layers"]["later_milestones"]["items"]
        rendered = json.dumps(milestones, ensure_ascii=False)
        self.assertNotIn("28-32 周", rendered)
        self.assertIn("32-36 周", rendered)
        self.assertIn("36 周后", rendered)
        self.assertIn("产后 0-42 天", rendered)

    def test_birth_journey_plan_does_not_personalize_from_skipped_or_unknown_answers(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="30周",
                    checkup_status="未上传产检记录",
                    current_symptoms="没有明显不舒服",
                    risk_factors="不清楚",
                    lifestyle_context="跳过",
                    feeding_ibclc_context="跳过",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        layers = result["card"]["card_json"]["planning_layers"]
        rendered = json.dumps(layers, ensure_ascii=False)
        self.assertIn("建立胎动、血压和水肿观察节奏", rendered)
        self.assertIn("补齐下次产检时间", rendered)
        self.assertNotIn("你已经提供产检信息", rendered)
        self.assertNotIn("把已做产检和待复查项整理成问题清单", rendered)
        self.assertNotIn("风险因素", rendered)
        self.assertNotIn("特殊情况", rendered)
        _assert_birth_journey_item_text_lengths(self, layers)

    def test_birth_journey_plan_keeps_all_items_but_limits_each_item_length(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="30周",
                    checkup_status="已做 NT、NIPT、大排畸，结果正常，还没预约糖耐",
                    current_symptoms="没有明显不舒服",
                    risk_factors="孕前 BMI 28",
                    lifestyle_context="久坐上班，晚上腿抽筋",
                    feeding_intention="母乳",
                    feeding_ibclc_context="计划母乳，想了解产后支持",
                    birth_path="剖宫产",
                    fetus_count="双胎",
                    birth_hospital="深圳市妇幼",
                    support_person="伴侣",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        layers = result["card"]["card_json"]["planning_layers"]
        self.assertGreater(len(layers["current_week_focus"]["items"]), 3)
        self.assertGreater(len(layers["next_7_days"]["items"]), 4)
        self.assertGreater(len(layers["next_2_4_weeks"]["items"]), 3)
        _assert_birth_journey_item_text_lengths(self, layers)

    def test_birth_journey_plan_is_saved_as_care_plan_artifact_when_user_id_exists(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                result = create_birth_journey_plan_card(
                    {
                        "plan_context": _plan_context(
                            due_date_or_week="30周",
                            birth_path="剖宫产",
                            support_person="伴侣",
                        ),
                        "scope": "full",
                    },
                    {"user_message": "", "user_id": "app-user", "message_sent_at": "2026-06-08T09:00:00+08:00"},
                )

                self.assertEqual(result["status"], "card_created")
                self.assertIsNotNone(result.get("plan"))
                plans = data_store.list_care_plan_artifacts(user_id="app-user")
                self.assertEqual(len(plans), 1)
                self.assertEqual(plans[0]["plan_type"], "birth_journey")
                self.assertEqual(plans[0]["title"], "孕期计划")
                self.assertEqual(plans[0]["payload"]["owner"]["current_week"], "孕30周")
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_birth_journey_plan_todo_completion_updates_saved_plan(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                created = create_birth_journey_plan_card(
                    {
                        "plan_context": _plan_context(
                            due_date_or_week="30周",
                            checkup_status="未上传产检记录",
                            current_symptoms="没有明显不舒服",
                        ),
                        "scope": "full",
                    },
                    {"user_message": "", "user_id": "app-user", "message_sent_at": "2026-06-08T09:00:00+08:00"},
                )
                plan_id = int(created["plan"]["plan_id"])

                updated = update_birth_journey_plan_todo_completion_for_user(
                    user_id="app-user",
                    plan_id=plan_id,
                    item_refs=["next7_01"],
                    completed=True,
                    source="app",
                )

                self.assertEqual(updated["status"], "todo_completion_updated")
                self.assertTrue(updated["side_effect_performed"])
                self.assertEqual(updated["updated_items"][0]["id"], "next7_01")
                self.assertTrue(updated["updated_items"][0]["completed"])
                saved = data_store.get_care_plan_artifact(user_id="app-user", plan_id=plan_id)
                next_7_items = saved["payload"]["planning_layers"]["next_7_days"]["items"]
                self.assertTrue(next_7_items[0]["completed"])
                self.assertEqual(next_7_items[0]["completed_source"], "app")

                context = build_request_context(
                    {
                        "user_message": "第一项完成了",
                        "user_id": "app-user",
                        "service_domain": "birth_prep",
                        "message_sent_at": "2026-06-08T10:00:00+08:00",
                    }
                )
                self.assertIn("next_7_days_todos", context)
                self.assertIn("1. [done] next7_01", context)
                milk_context = build_request_context(
                    {
                        "user_message": "看一下今天奶量",
                        "user_id": "app-user",
                        "service_domain": "milk-management",
                        "message_sent_at": "2026-06-08T10:00:00+08:00",
                    }
                )
                self.assertIn("active_care_plan_context:", milk_context)
                self.assertNotIn("next_7_days_todos", milk_context)
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_birth_journey_plan_todo_tool_updates_by_item_number(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                created = create_birth_journey_plan_card(
                    {
                        "plan_context": _plan_context(
                            due_date_or_week="30周",
                            checkup_status="未上传产检记录",
                            current_symptoms="没有明显不舒服",
                        ),
                        "scope": "full",
                    },
                    {"user_message": "", "user_id": "app-user", "message_sent_at": "2026-06-08T09:00:00+08:00"},
                )
                plan_id = int(created["plan"]["plan_id"])

                result = update_birth_journey_plan_todo(
                    {"item_ids": None, "item_numbers": [2], "item_refs": None, "completed": True},
                    {"user_message": "第二项已经做完了", "user_id": "app-user"},
                )

                self.assertEqual(result["tool_name"], "birth_journey_plan_todo_update")
                self.assertEqual(result["status"], "todo_completion_updated")
                saved = data_store.get_care_plan_artifact(user_id="app-user", plan_id=plan_id)
                next_7_items = saved["payload"]["planning_layers"]["next_7_days"]["items"]
                self.assertFalse(next_7_items[0]["completed"])
                self.assertTrue(next_7_items[1]["completed"])
                compact = model_tool_output({"ok": True, "tool_name": "birth_journey_plan_todo_update", "result": result})
                self.assertIn("最终回复只简短说明已同步", compact["final_response_instruction"])
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_existing_birth_journey_plan_is_reused_instead_of_recreated(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                first = create_birth_journey_plan_card(
                    {
                        "plan_context": _plan_context(
                            due_date_or_week="30周",
                            birth_path="顺产",
                            support_person="伴侣",
                        ),
                        "scope": "full",
                    },
                    {"user_message": "", "user_id": "app-user", "message_sent_at": "2026-06-08T09:00:00+08:00"},
                )
                first_plan_id = first["plan"]["plan_id"]

                reused = create_birth_journey_plan_card(
                    {"plan_context": {}, "scope": "full"},
                    {"user_message": "", "user_id": "app-user", "message_sent_at": "2026-06-09T09:00:00+08:00"},
                )

                self.assertEqual(reused["status"], "existing_plan_found")
                self.assertFalse(reused["side_effect_performed"])
                self.assertEqual(reused["plan"]["plan_id"], first_plan_id)
                self.assertEqual(reused["card"]["card_json"]["title"], "孕期计划")
                compact = model_tool_output({"ok": True, "tool_name": "birth_journey_plan_card_create", "result": reused})
                self.assertIn("不重复生成", compact["final_response_instruction"])
                plans = data_store.list_care_plan_artifacts(user_id="app-user")
                self.assertEqual(len(plans), 1)
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_birth_journey_plan_delete_requires_confirmation(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                saved = data_store.save_care_plan_artifact(
                    user_id="app-user",
                    plan_type="birth_journey",
                    title="孕期计划",
                    summary="孕晚期生产准备",
                    payload={"title": "孕期计划", "phases": []},
                    source_artifact_type="birth_journey_plan_card",
                )
                self.assertIsNotNone(saved)

                result = delete_birth_journey_plan(
                    {"confirmed": False},
                    {"user_message": "", "user_id": "app-user", "message_sent_at": "2026-06-09T09:00:00+08:00"},
                )

                self.assertEqual(result["status"], "needs_delete_confirmation")
                self.assertFalse(result["side_effect_performed"])
                self.assertEqual(len(data_store.list_care_plan_artifacts(user_id="app-user", status="active")), 1)
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_birth_journey_plan_delete_soft_deletes_active_plan(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                saved = data_store.save_care_plan_artifact(
                    user_id="app-user",
                    plan_type="birth_journey",
                    title="孕期计划",
                    summary="孕晚期生产准备",
                    payload={"title": "孕期计划", "phases": []},
                    source_artifact_type="birth_journey_plan_card",
                )
                self.assertIsNotNone(saved)

                result = delete_birth_journey_plan(
                    {"confirmed": True},
                    {"user_message": "", "user_id": "app-user", "message_sent_at": "2026-06-09T09:00:00+08:00"},
                )

                self.assertEqual(result["status"], "plan_deleted")
                self.assertTrue(result["side_effect_performed"])
                self.assertEqual(result["plan_type"], "birth_journey")
                self.assertEqual(result["plan_id"], saved["plan_id"])
                self.assertEqual(data_store.list_care_plan_artifacts(user_id="app-user", status="active"), [])
                self.assertEqual(len(data_store.list_care_plan_artifacts(user_id="app-user", status="deleted")), 1)
                compact = model_tool_output({"ok": True, "tool_name": "birth_journey_plan_delete", "result": result})
                self.assertIn("已删除孕期计划", compact["final_response_instruction"])
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_birth_journey_plan_delete_handles_missing_plan(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                result = delete_birth_journey_plan(
                    {"confirmed": True},
                    {"user_message": "", "user_id": "app-user", "message_sent_at": "2026-06-09T09:00:00+08:00"},
                )

                self.assertEqual(result["status"], "plan_not_found")
                self.assertFalse(result["side_effect_performed"])
                compact = model_tool_output({"ok": True, "tool_name": "birth_journey_plan_delete", "result": result})
                self.assertIn("没有找到 active 孕期计划", compact["final_response_instruction"])
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_request_context_includes_active_birth_journey_plan_memory(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"  # type: ignore[assignment]
            try:
                data_store.save_care_plan_artifact(
                    user_id="app-user",
                    plan_type="birth_journey",
                    title="孕期计划",
                    summary="从孕30周到产后 42 天的阶段路线图；当前阶段：孕晚期",
                    payload={
                        "title": "孕期计划",
                        "phases": [{"title": "孕晚期", "status": "current"}],
                    },
                    source_artifact_type="birth_journey_plan_card",
                )

                context = build_request_context(
                    {"user_message": "帮我制定孕期计划", "locale": "zh-CN", "user_id": "app-user"},
                    None,
                    ["birth-prep"],
                )

                self.assertIn("active_care_plan_context:", context)
                self.assertIn("birth_journey_plan: 已存在 active 孕期计划", context)
                self.assertIn("current_phase=孕晚期", context)
                self.assertIn("不要再次调用 birth_journey_plan_card_create 创建新计划", context)
            finally:
                data_store.DB_PATH = old_db_path  # type: ignore[assignment]

    def test_exposes_birth_journey_plan_tool(self) -> None:
        namespaces = {
            str(tool.get("name")): tool
            for tool in select_runtime_tools()
            if isinstance(tool, dict) and tool.get("type") == "namespace"
        }
        tool_names = [tool.get("name") for tool in namespaces["birth_prep"]["tools"]]

        self.assertIn("birth_journey_intake_manage", tool_names)
        self.assertIn("birth_journey_plan_card_create", tool_names)

    def test_birth_journey_plan_requires_every_survey_group_to_be_asked(self) -> None:
        result = create_birth_journey_plan_card(
            {"plan_context": {"due_date_or_week": "26周"}, "scope": "full"},
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "needs_required_context")
        self.assertIn("checkup_records", result["missing_fields"])
        self.assertIn("risk_factors", result["missing_fields"])
        self.assertIn("current_symptoms", result["missing_fields"])
        self.assertIn("lifestyle_context", result["missing_fields"])
        self.assertIn("feeding_ibclc_context", result["missing_fields"])
        self.assertNotIn("card", result)
        self.assertIn("只答知道的", result["data"]["confirmation_question"])
        self.assertIn("不清楚", result["data"]["confirmation_question"])
        self.assertIn("上传目前全部产检记录", result["data"]["confirmation_question"])

    def test_birth_journey_plan_merges_active_intake_state_when_model_passes_partial_context(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}
        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)
        basic = manage_birth_journey_intake(
            {"action": "submit_basic_info", "payload": {"current_week": "30周", "fetus_count": "单胎", "age": "30"}},
            {**inputs, "_birth_journey_intake_state": started["intake_state"]},
        )
        skipped = manage_birth_journey_intake(
            {"action": "skip_checkup_records", "payload": {}},
            {**inputs, "_birth_journey_intake_state": basic["intake_state"]},
        )
        risk = manage_birth_journey_intake(
            {"action": "submit_risk_factors", "payload": {"risk_factors": "不清楚"}},
            {**inputs, "_birth_journey_intake_state": skipped["intake_state"]},
        )
        symptoms = manage_birth_journey_intake(
            {"action": "submit_current_symptoms", "payload": {"current_symptoms": "没有明显不舒服"}},
            {**inputs, "_birth_journey_intake_state": risk["intake_state"]},
        )
        lifestyle = manage_birth_journey_intake(
            {"action": "submit_lifestyle_context", "payload": {"lifestyle_context": "睡眠不太好"}},
            {**inputs, "_birth_journey_intake_state": symptoms["intake_state"]},
        )

        result = create_birth_journey_plan_card(
            {
                "plan_context": {
                    "feeding_intention": "母乳",
                    "feeding_ibclc_context": "计划母乳喂养",
                },
                "scope": "full",
            },
            {**inputs, "_birth_journey_intake_state": lifestyle["intake_state"]},
        )

        self.assertEqual(result["status"], "card_created")
        card = result["card"]["card_json"]
        self.assertEqual(card["owner"]["due_date_or_week"], "30周")
        self.assertEqual(card["owner"]["current_week"], "孕30周")
        self.assertNotIn("missing_fields", result)

    def test_birth_journey_intake_flow_returns_plan_context_when_ready(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}
        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)

        self.assertEqual(started["status"], "in_progress")
        self.assertEqual(started["next_step"], "basic_info_form")
        self.assertEqual(started["form"]["id"], "birth_journey_basic_info_intake")

        state = started["intake_state"]
        basic = manage_birth_journey_intake(
            {"action": "submit_basic_info", "payload": {"current_week": "28周", "fetus_count": "单胎", "age": "32"}},
            {**inputs, "_birth_journey_intake_state": state},
        )
        state = basic["intake_state"]
        uploaded = manage_birth_journey_intake(
            {"action": "mark_checkup_records_uploaded", "payload": {}},
            {**inputs, "_birth_journey_intake_state": state},
        )
        state = uploaded["intake_state"]
        risk = manage_birth_journey_intake(
            {"action": "submit_risk_factors", "payload": {"risk_factors": "不清楚"}},
            {**inputs, "_birth_journey_intake_state": state},
        )
        state = risk["intake_state"]
        symptoms = manage_birth_journey_intake(
            {"action": "submit_current_symptoms", "payload": {"current_symptoms": "没有明显不舒服"}},
            {**inputs, "_birth_journey_intake_state": state},
        )
        state = symptoms["intake_state"]
        lifestyle = manage_birth_journey_intake(
            {"action": "submit_lifestyle_context", "payload": {"lifestyle_context": "久坐上班，伴侣支持"}},
            {**inputs, "_birth_journey_intake_state": state},
        )
        state = lifestyle["intake_state"]
        feeding = manage_birth_journey_intake(
            {"action": "submit_feeding_context", "payload": {"feeding_ibclc_context": "计划母乳，想了解吸奶和背奶"}},
            {**inputs, "_birth_journey_intake_state": state},
        )

        self.assertEqual(feeding["status"], "ready_to_generate")
        self.assertEqual(feeding["next_step"], "generate_plan")
        self.assertEqual(feeding["plan_context"]["due_date_or_week"], "28周")
        self.assertEqual(feeding["plan_context"]["checkup_records_uploaded"], "是")
        self.assertIn("久坐上班", feeding["plan_context"]["lifestyle_context"])

    def test_birth_journey_intake_skip_checkup_records_does_not_mark_uploaded(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}
        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)
        basic = manage_birth_journey_intake(
            {"action": "submit_basic_info", "payload": {"current_week": "30周", "fetus_count": "单胎"}},
            {**inputs, "_birth_journey_intake_state": started["intake_state"]},
        )

        skipped = manage_birth_journey_intake(
            {"action": "skip_checkup_records", "payload": {}},
            {**inputs, "_birth_journey_intake_state": basic["intake_state"]},
        )

        self.assertEqual(skipped["status"], "in_progress")
        self.assertEqual(skipped["next_step"], "risk_question")
        self.assertFalse(skipped["intake_state"]["checkup_records_uploaded"])
        state = skipped["intake_state"]

        risk = manage_birth_journey_intake(
            {"action": "submit_risk_factors", "payload": {"risk_factors": "不清楚"}},
            {**inputs, "_birth_journey_intake_state": state},
        )
        symptoms = manage_birth_journey_intake(
            {"action": "submit_current_symptoms", "payload": {"current_symptoms": "没有明显不舒服"}},
            {**inputs, "_birth_journey_intake_state": risk["intake_state"]},
        )
        lifestyle = manage_birth_journey_intake(
            {"action": "submit_lifestyle_context", "payload": {"lifestyle_context": "跳过"}},
            {**inputs, "_birth_journey_intake_state": symptoms["intake_state"]},
        )
        feeding = manage_birth_journey_intake(
            {"action": "submit_feeding_context", "payload": {"feeding_ibclc_context": "跳过"}},
            {**inputs, "_birth_journey_intake_state": lifestyle["intake_state"]},
        )

        self.assertEqual(feeding["status"], "ready_to_generate")
        self.assertEqual(feeding["plan_context"]["checkup_status"], "未上传产检记录")
        self.assertNotIn("checkup_records_uploaded", feeding["plan_context"])

    def test_birth_journey_basic_info_form_omits_date_fields_and_prefills_city(self) -> None:
        inputs = {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00", "_birth_journey_intake_state": {}}

        started = manage_birth_journey_intake({"action": "start", "payload": {}}, inputs)

        fields = started["form"]["fields"]
        labels = [field["label"] for field in fields]
        ids = [field["id"] for field in fields]
        self.assertNotIn("末次月经", labels)
        self.assertNotIn("预产期", labels)
        self.assertNotIn("last_menstrual_period", ids)
        self.assertNotIn("due_date", ids)
        self.assertIn("是否 IVF（体外受精）", labels)
        required_by_id = {field["id"]: field["required"] for field in fields}
        self.assertTrue(required_by_id["current_week"])
        self.assertTrue(required_by_id["fetus_count"])
        self.assertTrue(required_by_id["age"])
        self.assertFalse(required_by_id["ivf"])
        self.assertEqual(started["form"]["description"], "")
        self.assertEqual(started["form"]["default_values"]["city_or_country"], "深圳")

        with_region = manage_birth_journey_intake(
            {"action": "start", "payload": {}},
            {**inputs, "user_profile": {"region": "广州"}},
        )
        self.assertEqual(with_region["form"]["default_values"]["city_or_country"], "广州")

        with_pregnancy_week = manage_birth_journey_intake(
            {"action": "start", "payload": {}},
            {**inputs, "user_profile": {"birth_prep_due_date_or_week": "孕25周"}},
        )
        self.assertEqual(with_pregnancy_week["form"]["default_values"]["current_week"], "孕25周")

        with_profile_defaults = manage_birth_journey_intake(
            {"action": "start", "payload": {}},
            {
                **inputs,
                "user_profile": {
                    "age": 32,
                    "birth_prep_ivf": "是",
                    "birth_prep_fetus_count": "双胎",
                    "birth_prep_birth_hospital": "深圳市妇幼",
                },
            },
        )
        profile_defaults = with_profile_defaults["form"]["default_values"]
        self.assertEqual(profile_defaults["age"], "32")
        self.assertEqual(profile_defaults["ivf"], "是")
        self.assertEqual(profile_defaults["fetus_count"], "双胎")
        self.assertEqual(profile_defaults["birth_hospital"], "深圳市妇幼")

    def test_birth_journey_plan_accepts_unknown_birth_path_and_no_support_person(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="26周",
                    birth_path="还没确定",
                    support_person="暂时没有",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        card = result["card"]["card_json"]
        self.assertNotIn("birth_path", card["owner"])
        self.assertEqual(card["owner"]["support_person"], "暂时没有")

    def test_birth_journey_late_pregnancy_wording_avoids_aiish_pile_up_phrase(self) -> None:
        result = create_birth_journey_plan_card(
            {
                "plan_context": _plan_context(
                    due_date_or_week="30周",
                    birth_path="顺产",
                    support_person="伴侣",
                ),
                "scope": "full",
            },
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "card_created")
        card = result["card"]["card_json"]
        rendered = json.dumps(card, ensure_ascii=False)
        self.assertIn("距离生产越来越近，提前做好准备会让临产和住院过程更顺利。", rendered)
        self.assertNotIn("铺太多", rendered)

    def test_model_tool_output_compacts_birth_journey_card(self) -> None:
        raw = {
            "ok": True,
            "tool_name": "birth_journey_plan_card_create",
            "result": {
                "status": "card_created",
                "card": {
                    "card_type": "birth_journey_plan_card",
                    "schema_version": "1.0",
                    "card_json": {
                        "title": "孕期计划",
                        "phases": [
                            {
                                "title": "孕中期",
                                "status": "current",
                                "watchouts": ["这个阶段不用把生产准备一次做完。"],
                                "actions": ["列出下次产检最想确认的 3-5 个问题。"],
                                "comate_help": ["生成产检问题清单。"],
                            }
                        ],
                        "planning_layers": {
                            "current_week_focus": {
                                "items": [
                                    {"title": "整理产检问题", "reason": "把要问医生的问题先列出来。"},
                                ],
                            },
                            "next_7_days": {
                                "items": [
                                    {"id": "next7_01", "title": "整理产检信息", "completed": False},
                                    {"id": "next7_02", "title": "固定胎动观察规则", "completed": False},
                                ],
                            },
                        },
                        "next_action": {"label": "整理产检问题", "send_text": "帮我整理下次产检要问的 3-5 个问题"},
                    },
                },
            },
        }

        compact = model_tool_output(raw)

        self.assertEqual(compact["status"], "card_created")
        self.assertEqual(compact["card"], {"card_type": "birth_journey_plan_card", "schema_version": "1.0", "created": True})
        self.assertIn("最终回复用 2-4 句中文自然组织语言", compact["final_response_instruction"])
        self.assertIn("接下来 7 天行动清单", compact["final_response_instruction"])
        self.assertIn("完成项追问", compact["final_response_instruction"])
        self.assertIn("不要机械照抄", compact["final_response_instruction"])
        self.assertIn("计划已生成，可以在宝宝和我页面查看", compact["final_response_instruction"])
        self.assertIn("接下来我会按照计划主动提醒你哦", compact["final_response_instruction"])
        self.assertIn("不要提本周重点、当前优先级或当前阶段总结", compact["final_response_instruction"])
        self.assertIn("不要补充外部资料、来源引用或引用编号", compact["final_response_instruction"])
        self.assertIn("不要使用“卡片”这类界面形式词", compact["final_response_instruction"])
        self.assertIn("不要再输出“我先帮你生成”或“我整理好了”", compact["final_response_instruction"])
        self.assertNotIn("孕期计划我整理好了", compact["final_response_instruction"])
        self.assertNotIn("当前优先级：整理产检问题", compact["final_response_instruction"])
        self.assertNotIn("因为把要问医生的问题先列出来", compact["final_response_instruction"])
        self.assertIn("1. 整理产检信息；2. 固定胎动观察规则", compact["final_response_instruction"])
        self.assertIn("是否有已经完成的事项", compact["final_response_instruction"])
        self.assertNotIn("接下来我可以先陪你整理产检问题", compact["final_response_instruction"])
        self.assertNotIn("当前阶段是", compact["final_response_instruction"])
        self.assertNotIn("重点先留意", compact["final_response_instruction"])
        self.assertNotIn("准备动作先从这件事开始", compact["final_response_instruction"])
        self.assertNotIn("assistant_followup", compact)
        self.assertNotIn("phases", json.dumps(compact, ensure_ascii=False))

    def test_model_tool_output_for_birth_journey_missing_context_only_asks_question(self) -> None:
        raw = {
            "ok": True,
            "tool_name": "birth_journey_plan_card_create",
            "result": {
                "status": "needs_required_context",
                "summary": "生成孕期计划前，需要先完成分层信息采集。",
                "missing_fields": ["due_date_or_week", "birth_path", "support_person"],
                "data": {
                    "confirmation_question": "我先确认 3 件事再生成计划。",
                },
            },
        }

        compact = model_tool_output(raw)

        self.assertEqual(compact["status"], "needs_required_context")
        self.assertEqual(compact["missing_fields"], ["due_date_or_week", "birth_path", "support_person"])
        self.assertIn("最终回复只向用户补问", compact["final_response_instruction"])
        self.assertNotIn("card", compact)


if __name__ == "__main__":
    unittest.main()
