from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from momcozy_agent.agents import model_tool_output
from momcozy_agent.contexts import build_request_context
from momcozy_agent.services import data_store
from momcozy_agent.tool_handlers.cards import create_birth_journey_plan_card, delete_birth_journey_plan
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
        self.assertEqual(card["title"], "生产全过程计划")
        self.assertEqual(card["owner"]["current_week"], "孕25周")
        self.assertEqual(card["owner"]["estimated_due_date"], "2026/09/13")
        layers = card["planning_layers"]
        self.assertEqual(layers["current_week"], 25)
        self.assertIn("你的本周重点", layers["current_week_focus"]["title"])
        self.assertTrue(layers["current_week_focus"]["items"])
        self.assertTrue(layers["next_7_days"]["items"])
        self.assertTrue(layers["next_2_4_weeks"]["items"])
        self.assertTrue(layers["later_milestones"]["items"])

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
                self.assertEqual(plans[0]["title"], "生产全过程计划")
                self.assertEqual(plans[0]["payload"]["owner"]["current_week"], "孕30周")
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
                self.assertEqual(reused["card"]["card_json"]["title"], "生产全过程计划")
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
                    title="生产全过程计划",
                    summary="孕晚期生产准备",
                    payload={"title": "生产全过程计划", "phases": []},
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
                    title="生产全过程计划",
                    summary="孕晚期生产准备",
                    payload={"title": "生产全过程计划", "phases": []},
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
                self.assertIn("已删除生产全过程计划", compact["final_response_instruction"])
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
                self.assertIn("没有找到 active 生产全过程计划", compact["final_response_instruction"])
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
                    title="生产全过程计划",
                    summary="从孕30周到产后 42 天的阶段路线图；当前阶段：孕晚期",
                    payload={
                        "title": "生产全过程计划",
                        "phases": [{"title": "孕晚期", "status": "current"}],
                    },
                    source_artifact_type="birth_journey_plan_card",
                )

                context = build_request_context(
                    {"user_message": "帮我制定生产全过程计划", "locale": "zh-CN", "user_id": "app-user"},
                    None,
                    ["birth-prep"],
                )

                self.assertIn("active_care_plan_context:", context)
                self.assertIn("birth_journey_plan: 已存在 active 生产全过程计划", context)
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

        self.assertIn("birth_journey_plan_card_create", tool_names)

    def test_birth_journey_plan_requires_every_survey_group_to_be_asked(self) -> None:
        result = create_birth_journey_plan_card(
            {"plan_context": {"due_date_or_week": "26周"}, "scope": "full"},
            {"user_message": "", "message_sent_at": "2026-06-01T12:00:00+08:00"},
        )

        self.assertEqual(result["status"], "needs_required_context")
        self.assertIn("birth_path", result["missing_fields"])
        self.assertIn("support_person", result["missing_fields"])
        self.assertIn("checkup_status", result["missing_fields"])
        self.assertIn("feeding_ibclc_context", result["missing_fields"])
        self.assertNotIn("card", result)
        self.assertIn("只答知道的", result["data"]["confirmation_question"])
        self.assertIn("不清楚", result["data"]["confirmation_question"])
        self.assertIn("是否计划母乳", result["data"]["confirmation_question"])

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
                        "title": "生产全过程计划",
                        "phases": [
                            {
                                "title": "孕中期",
                                "status": "current",
                                "watchouts": ["这个阶段不用把生产准备一次做完。"],
                                "actions": ["列出下次产检最想确认的 3-5 个问题。"],
                                "comate_help": ["生成产检问题清单。"],
                            }
                        ],
                        "next_action": {"label": "整理产检问题", "send_text": "帮我整理下次产检要问的 3-5 个问题"},
                    },
                },
            },
        }

        compact = model_tool_output(raw)

        self.assertEqual(compact["status"], "card_created")
        self.assertEqual(compact["card"], {"card_type": "birth_journey_plan_card", "schema_version": "1.0", "created": True})
        self.assertIn("最终回复用 2-4 句中文自然组织语言", compact["final_response_instruction"])
        self.assertIn("不要机械照抄", compact["final_response_instruction"])
        self.assertIn("计划已生成，可以在宝宝和我页面查看", compact["final_response_instruction"])
        self.assertIn("接下来我会按照计划主动提醒你哦", compact["final_response_instruction"])
        self.assertIn("不要使用“卡片”这类界面形式词", compact["final_response_instruction"])
        self.assertIn("不要再输出“我先帮你生成”或“我整理好了”", compact["final_response_instruction"])
        self.assertNotIn("生产全过程计划我整理好了", compact["final_response_instruction"])
        self.assertIn("你现在在孕中期", compact["final_response_instruction"])
        self.assertIn("先不用把生产准备一次做完", compact["final_response_instruction"])
        self.assertIn("准备上先列出下次产检最想确认的 3-5 个问题", compact["final_response_instruction"])
        self.assertIn("接下来我可以先陪你整理产检问题", compact["final_response_instruction"])
        self.assertIn("题。\n\n接下来我可以先陪你整理产检问题。", compact["final_response_instruction"])
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
                "summary": "生成生产全过程计划前，需要先确认孕期、分娩方式和支持人。",
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
