from __future__ import annotations

import os
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

MILK_MANAGEMENT_TOOLS_DB_PATH = os.path.join(tempfile.mkdtemp(prefix="momcozy-agent-tests-"), "milk_management.db")
os.environ["MILK_DB_PATH"] = MILK_MANAGEMENT_TOOLS_DB_PATH

from momcozy_agent.services.milk_management.calendar import (
    apply_calendar_adjustment,
    apply_calendar_reschedule,
    preview_calendar_adjustment,
    preview_day_reschedule,
)
from momcozy_agent.services.milk_management.clinical_assessment import evaluate_lactation_clinical_status
from momcozy_agent.services.milk_management.db import fetch_all, transaction
from momcozy_agent.services.milk_management.growth_mutation import mutate_infant_growth
from momcozy_agent.services.milk_management.assessment import evaluate_milk_status
from momcozy_agent.services.milk_management.plan import apply_milk_plan, preview_milk_plan, validate_milk_plan
from momcozy_agent.services.milk_management.status import query_milk_status
from momcozy_agent.services.milk_management.task_completion import complete_milk_task
from momcozy_agent.agents import artifact_events_from_tool_result, model_tool_output, safe_tool_result
from momcozy_agent.tool_handlers.milk_management import (
    _milk_analysis_headline,
    _milk_next_step,
    _milk_trend_text,
    execute_milk_management_tool,
)


class MilkManagementToolTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["MILK_DB_PATH"] = MILK_MANAGEMENT_TOOLS_DB_PATH

    def test_complete_pumping_without_amount_marks_done_without_creating_record(self) -> None:
        uid, _ = _seed_user("complete-no-amount")
        _add_task(uid, task_id=1, content="吸奶", item_type="吸奶", is_milk_pump=1)

        result = complete_milk_task(
            user_id=uid,
            operation="complete",
            target_date="2026-05-14",
            task_id=1,
            record_kind=None,
            amount_ml=None,
            duration_minutes=None,
            occurred_at=None,
            title=None,
            delete_linked_record=True,
            idempotency_key="complete-no-amount",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["task"]["finish"], "true")
        self.assertEqual(_scalar("SELECT COUNT(*) FROM pumping_log WHERE user_id = ?", (uid,)), 0)

    def test_cancel_complete_deletes_only_synced_feeding_record(self) -> None:
        uid, infant_id = _seed_user("cancel-keeps-manual")
        _add_task(uid, task_id=1, content="亲喂", item_type="亲喂", is_milk_pump=0)
        with transaction() as conn:
            conn.execute(
                """
                INSERT INTO feeding_log(user_id, infant_id, feed_time, feed_type, feed_milk_volum, feed_action, feeding_title)
                VALUES (?, ?, '2026-05-14 09:00:00', '亲喂', 18, 0, '亲喂')
                """,
                (uid, infant_id),
            )

        complete_milk_task(
            user_id=uid,
            operation="complete",
            target_date="2026-05-14",
            task_id=1,
            record_kind="nursing",
            amount_ml=None,
            duration_minutes=15,
            occurred_at=None,
            title=None,
            delete_linked_record=True,
            idempotency_key="complete-nursing",
        )
        result = complete_milk_task(
            user_id=uid,
            operation="cancel_complete",
            target_date="2026-05-14",
            task_id=1,
            record_kind=None,
            amount_ml=None,
            duration_minutes=None,
            occurred_at=None,
            title=None,
            delete_linked_record=True,
            idempotency_key="cancel-nursing",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(_scalar("SELECT COUNT(*) FROM feeding_log WHERE user_id = ? AND feed_action = 0", (uid,)), 1)
        self.assertEqual(_scalar("SELECT COUNT(*) FROM feeding_log WHERE user_id = ? AND feed_action = 1", (uid,)), 0)
        self.assertEqual(_scalar("SELECT COUNT(*) FROM pumping_log WHERE user_id = ? AND pump_source = 2", (uid,)), 0)

    def test_growth_create_is_idempotent_for_same_key(self) -> None:
        uid, infant_id = _seed_user("growth-idempotent")

        first = mutate_infant_growth(
            user_id=uid,
            operation="create",
            infant_id=infant_id,
            height_cm=55,
            weight_kg=4.6,
            head_cm=38,
            target_date="2026-05-14",
            history_limit=5,
            idempotency_key="same-growth-key",
        )
        second = mutate_infant_growth(
            user_id=uid,
            operation="create",
            infant_id=infant_id,
            height_cm=56,
            weight_kg=4.8,
            head_cm=39,
            target_date="2026-05-14",
            history_limit=5,
            idempotency_key="same-growth-key",
        )

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(second["status"], "infant_growth_idempotent_replay")
        self.assertEqual(first["data"]["record"]["growth_id"], second["data"]["record"]["growth_id"])
        self.assertEqual(_scalar("SELECT COUNT(*) FROM infant_growth_log WHERE user_id = ?", (uid,)), 1)

    def test_status_query_includes_tasks_for_growth_section_when_requested(self) -> None:
        uid, _ = _seed_user("status-growth-tasks")
        _add_task(uid, task_id=1, content="吸奶", item_type="吸奶", is_milk_pump=1)

        result = query_milk_status(
            user_id=uid,
            section="growth",
            target_date="2026-05-14",
            trend_days=7,
            growth_history_limit=5,
            include_tasks=True,
        )

        self.assertTrue(result["ok"])
        self.assertIn("tasks", result["data"])
        self.assertEqual(len(result["data"]["tasks"]["task_list"]), 1)

    def test_status_query_returns_mom_baby_tab_card_for_full_status_page(self) -> None:
        uid, infant_id = _seed_user("status-tab-card")
        _add_pumping_rows(uid, "2026-05-14", ["08:00", "12:00"])
        _add_task(uid, task_id=1, content="吸奶", item_type="吸奶", is_milk_pump=1)
        _add_task(uid, task_id=2, content="亲喂", item_type="亲喂", is_milk_pump=0, start_time="11:00")
        mutate_infant_growth(
            user_id=uid,
            operation="create",
            infant_id=infant_id,
            height_cm=56,
            weight_kg=4.8,
            head_cm=39,
            target_date="2026-05-14",
            history_limit=5,
            idempotency_key="status-tab-growth",
        )

        result = query_milk_status(
            user_id=uid,
            section="all",
            target_date="2026-05-14",
            trend_days=7,
            growth_history_limit=5,
            include_tasks=True,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["card"]["card_type"], "mom_baby_status_card")
        tabs = result["card"]["card_json"]["tabs"]
        self.assertEqual([tab["title"] for tab in tabs], ["妈妈数字分身", "宝宝数字分身"])
        self.assertEqual(result["data"]["status_page_tabs"], tabs)
        self.assertEqual(tabs[0]["sections"][0]["title"], "今日泌乳")
        self.assertEqual(tabs[1]["sections"][0]["title"], "今日喂养")
        self.assertEqual(tabs[0]["sections"][0]["metrics"][0]["value"], "140 ml")
        self.assertEqual(tabs[1]["sections"][1]["metrics"][0]["value"], "4.8 kg")

    def test_increase_plan_preview_is_saveable_when_current_frequency_is_high(self) -> None:
        uid, _ = _seed_user("increase-preview-saveable")
        _add_pumping_rows(
            uid,
            "2026-05-13",
            ["00:55", "06:30", "09:30", "11:30", "13:30", "16:30", "19:30", "22:30"],
        )

        result = preview_milk_plan(
            user_id=uid,
            plan_type="increase_milk",
            plan_days=3,
            as_of_time="2026-05-14 12:00:00",
            options={
                "prepared_assessment": {
                    "pumping_summary": {"count": 8, "total_ml": 560},
                    "feeding_summary": {"type_counts": {"亲喂": 2}},
                    "window": {"window_days": 1},
                    "milk_normality": {
                        "overall_status": "under_supply_alert",
                        "days": [
                            {
                                "ok": True,
                                "date": "2026-05-13",
                                "estimated_daily_milk_ml": 560,
                                "yield_reference": {"p15": 720, "p85": 950},
                            }
                        ],
                    },
                },
                "prepared_growth_assessment": {"status": "normal"},
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "plan_preview_ready")
        self.assertTrue(result["data"]["requires_confirmation"])
        self.assertTrue(result["data"]["validation"]["valid"])
        draft = result["data"]["draft"]
        first_template = draft["daily_schedule_templates"][0]
        times = [item["time"] for item in first_template["items"]]
        self.assertGreater(len(times), draft["plan_rules"]["current_pumping_count"])
        self.assertLessEqual(_max_linear_gap_minutes(times), 300)

        validation = validate_milk_plan(user_id=uid, plan=draft)
        self.assertTrue(validation["data"]["valid"])

    def test_increase_plan_preview_prefers_daytime_pump_tasks(self) -> None:
        uid, _ = _seed_user("increase-daytime")
        _add_pumping_rows(
            uid,
            "2026-05-13",
            ["00:30", "03:30", "06:30", "09:30", "12:30", "15:30", "18:30", "21:30"],
        )

        result = preview_milk_plan(
            user_id=uid,
            plan_type="increase_milk",
            plan_days=3,
            as_of_time="2026-05-14 12:00:00",
            options={
                "prepared_assessment": {
                    "pumping_summary": {"count": 8, "total_ml": 560},
                    "feeding_summary": {"type_counts": {}},
                    "window": {"window_days": 1},
                    "milk_normality": {
                        "overall_status": "under_supply_alert",
                        "days": [
                            {
                                "ok": True,
                                "date": "2026-05-13",
                                "estimated_daily_milk_ml": 560,
                                "yield_reference": {"p15": 920, "p85": 1100},
                            }
                        ],
                    },
                },
                "prepared_growth_assessment": {"status": "normal"},
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "plan_preview_ready")
        draft = result["data"]["draft"]
        first_template = draft["daily_schedule_templates"][0]
        times = [item["time"] for item in first_template["items"]]
        self.assertLessEqual(_night_task_count(times), 1)
        pp_times = [item["time"] for item in first_template["items"] if item.get("kind") == "pp"]
        self.assertTrue(pp_times)
        self.assertFalse(_is_night_time_text(pp_times[0]))
        self.assertTrue(result["data"]["validation"]["valid"])

    def test_decrease_plan_preview_prefers_daytime_pump_tasks(self) -> None:
        uid, _ = _seed_user("decrease-daytime")
        _add_pumping_rows(
            uid,
            "2026-05-13",
            ["00:30", "03:30", "06:30", "09:30", "12:30", "15:30", "18:30", "21:30"],
        )

        result = preview_milk_plan(
            user_id=uid,
            plan_type="decrease_milk",
            plan_days=14,
            as_of_time="2026-05-14 12:00:00",
            options={
                "observed_persistent_abnormal": True,
                "prepared_assessment": {
                    "pumping_summary": {"count": 8, "total_ml": 960},
                    "feeding_summary": {"type_counts": {}},
                    "window": {"window_days": 1},
                    "milk_normality": {
                        "overall_status": "over_supply_alert",
                        "days": [
                            {
                                "ok": True,
                                "date": "2026-05-13",
                                "estimated_daily_milk_ml": 960,
                                "yield_reference": {"p15": 620, "p85": 820},
                            }
                        ],
                    },
                },
                "prepared_growth_assessment": {"status": "normal"},
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "plan_preview_ready")
        draft = result["data"]["draft"]
        for template in draft["daily_schedule_templates"]:
            times = [item["time"] for item in template.get("items", [])]
            self.assertLessEqual(_night_task_count(times), 1)
        self.assertTrue(result["data"]["validation"]["valid"])

    def test_assessment_separates_calendar_tasks_from_pumping_records(self) -> None:
        uid, _ = _seed_user("assessment-calendar-vs-records")
        for index, time in enumerate(["00:00", "03:00", "06:00", "09:00", "12:00", "15:00", "18:00", "21:00"], start=1):
            _add_task(
                uid,
                task_id=index,
                content="吸奶",
                item_type="吸奶",
                is_milk_pump=1,
                target_date="2026-05-13",
                start_time=time,
                finish="true",
            )
        _add_pumping_rows(uid, "2026-05-13", ["06:00", "09:00", "12:00", "18:00", "21:00"])

        result = evaluate_milk_status(
            user_id=uid,
            as_of_time="2026-05-14 12:00:00",
            window_days=1,
            include_today=False,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["pumping_summary"]["count"], 5)
        calendar_summary = result["data"]["calendar_task_summary"]
        self.assertEqual(calendar_summary["pump_task_count"], 8)
        self.assertEqual(calendar_summary["completed_pump_task_count"], 8)
        self.assertEqual(calendar_summary["average_completed_pump_tasks_per_day"], 8.0)

    def test_assessment_tool_returns_comprehensive_text_assessment(self) -> None:
        uid, _ = _seed_user("assessment-card")
        for index, time in enumerate(["00:00", "03:00", "06:00", "09:00", "12:00", "15:00", "18:00", "21:00"], start=1):
            _add_task(
                uid,
                task_id=index,
                content="吸奶",
                item_type="吸奶",
                is_milk_pump=1,
                target_date="2026-05-13",
                start_time=time,
                finish="true",
            )
        _add_pumping_rows(uid, "2026-05-13", ["06:00", "09:00", "12:00", "18:00", "21:00"])

        result = execute_milk_management_tool(
            {
                "_tool_name": "milk_assessment_evaluate",
                "user_id": uid,
                "as_of_time": "2026-05-14 12:00:00",
                "window_days": 7,
                "include_today": False,
                "infant_signals": {"wet_diapers_24h": 6, "baby_state": "精神状态正常"},
                "maternal_symptoms": {"fever": False, "breast_redness": False, "lump_or_hard_area": False},
            },
            {"user_message": "分析最近吸奶情况", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertNotIn("card", result)
        self.assertIn("data", result)
        self.assertIn("clinical_assessment", result["data"])
        compact = model_tool_output({"ok": True, "tool_name": "milk_assessment_evaluate", "result": result})
        self.assertNotIn("card", compact)
        self.assertIn("user_context", compact)
        self.assertNotIn("status", compact)
        self.assertNotIn("assistant_followup", compact)
        reply_context = compact["user_context"]
        self.assertIn("最近整体偏低", reply_context["判断结果"]["奶量产出"])
        self.assertTrue(any("近几天含亲喂估算" in item for item in reply_context["判断依据"]))
        self.assertTrue(any("记录大致可用" in item for item in reply_context["判断结果"]["记录情况"]))
        self.assertIn("比较安心", reply_context["判断结果"]["宝宝摄入信号"])
        self.assertIn("暂时没有明显发热", reply_context["判断结果"]["妈妈状态"])
        self.assertIn("问用户是否现在制定温和追奶计划", reply_context["下一步要确认的信息"])
        self.assertIn("可以先征求用户是否进入计划制定", reply_context["为什么要问这些"])
        self.assertIn("制定温和追奶计划", reply_context["适合的下一步计划"])
        self.assertTrue(any("宝宝需求变高" in item for item in reply_context["不能这样推断"]))
        self.assertTrue(all("缩短最长间隔" not in item for item in reply_context["下一步要确认的信息"]))
        self.assertNotIn("记录完整后的推荐承接", reply_context)
        self.assertNotIn("接下来怎么调", reply_context)
        self.assertEqual(compact["适合的下一步"][0], "询问是否制定温和追奶计划")
        self.assertIn("用户同意后生成计划草稿", compact["适合的下一步"])
        self.assertIn("只需要文本回复", compact["final_response_instruction"])
        self.assertIn("用户只回答了其中一部分", compact["final_response_instruction"])
        self.assertIn("明确下一步", compact["final_response_instruction"])
        self.assertIn("user_context 里显示适合进入计划", compact["final_response_instruction"])
        self.assertIn("不要把宝宝实际需求变高说成奶量产出偏低的原因", compact["final_response_instruction"])
        self.assertNotIn("固定模板", json.dumps(reply_context, ensure_ascii=False))
        self.assertNotIn("工具建议话术", compact)
        self.assertNotIn("assessment", compact["final_response_instruction"])
        self.assertNotIn("data", compact)
        json.dumps(result, ensure_ascii=False)

    def test_assessment_tool_requires_clinical_context_before_comprehensive_analysis(self) -> None:
        uid, _ = _seed_user("assessment-needs-clinical-context")
        _add_pumping_rows(uid, "2026-05-13", ["06:00", "09:00", "12:00", "18:00", "21:00"])

        result = execute_milk_management_tool(
            {
                "_tool_name": "milk_assessment_evaluate",
                "user_id": uid,
                "as_of_time": "2026-05-14 12:00:00",
                "window_days": 7,
                "include_today": False,
            },
            {"user_message": "分析最近吸奶情况", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "needs_clinical_context")
        self.assertIn("assistant_followup", result)
        self.assertIn("尿布", result["assistant_followup"]["message"])
        self.assertIn("乳房", result["assistant_followup"]["message"])
        self.assertNotIn("card", result)
        self.assertIn("clinical_assessment", result["data"])
        self.assertIn("control_suggestion", result["data"])
        self.assertEqual(result["data"]["workflow_intent"], "milk_analysis")
        self.assertIn("继续调用 milk_assessment_evaluate", result["data"]["continuation_instruction"])
        self.assertIn("不要因为出现", result["data"]["continuation_instruction"])
        self.assertEqual(result["data"]["missing_fields"], ["infant_signals", "maternal_symptoms"])
        self.assertIn("再继续分析吸奶和奶量", result["summary"])
        compact = model_tool_output({"ok": True, "tool_name": "milk_assessment_evaluate", "result": result})
        self.assertNotIn("card", compact)
        self.assertNotIn("status", compact)
        self.assertNotIn("workflow_intent", compact)
        self.assertNotIn("continuation_instruction", compact)
        self.assertNotIn("assistant_followup", compact)
        self.assertEqual(
            compact["需要继续确认"]["还需要确认"],
            ["宝宝近 24 小时尿布、精神和吃奶表现", "妈妈有没有发热、乳房红肿、硬块或疼痛加重"],
        )
        self.assertIn("尿布", " ".join(compact["需要继续确认"]["可以这样问用户"]))
        self.assertNotIn("工具建议话术", compact)
        self.assertIn("不要硬下结论", compact["final_response_instruction"])
        self.assertIn("一轮只问一个关键问题", compact["final_response_instruction"])
        self.assertIn("继续问未回答项", compact["final_response_instruction"])
        self.assertIn("明确问题或明确下一步", compact["final_response_instruction"])
        json.dumps(result, ensure_ascii=False)

    def test_assessment_tool_keeps_asking_when_only_infant_context_is_answered(self) -> None:
        uid, _ = _seed_user("assessment-missing-maternal-context")
        _add_pumping_rows(uid, "2026-05-13", ["06:00", "09:00", "12:00", "18:00", "21:00"])

        result = execute_milk_management_tool(
            {
                "_tool_name": "milk_assessment_evaluate",
                "user_id": uid,
                "as_of_time": "2026-05-14 12:00:00",
                "window_days": 7,
                "include_today": False,
                "workflow_intent": "milk_analysis",
                "infant_signals": {"wet_diapers_24h": 6, "baby_state": "正常"},
            },
            {"user_message": "宝宝尿布和精神都正常", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertEqual(result["status"], "needs_clinical_context")
        self.assertEqual(result["data"]["missing_fields"], ["maternal_symptoms"])
        self.assertIn("乳房", " ".join(result["data"]["suggested_questions"]))
        self.assertNotIn("尿布", " ".join(result["data"]["suggested_questions"]))
        compact = model_tool_output({"ok": True, "tool_name": "milk_assessment_evaluate", "result": result})
        self.assertEqual(compact["需要继续确认"]["还需要确认"], ["妈妈有没有发热、乳房红肿、硬块或疼痛加重"])

    def test_assessment_tool_keeps_asking_when_only_maternal_context_is_answered(self) -> None:
        uid, _ = _seed_user("assessment-missing-infant-context")
        _add_pumping_rows(uid, "2026-05-13", ["06:00", "09:00", "12:00", "18:00", "21:00"])

        result = execute_milk_management_tool(
            {
                "_tool_name": "milk_assessment_evaluate",
                "user_id": uid,
                "as_of_time": "2026-05-14 12:00:00",
                "window_days": 7,
                "include_today": False,
                "workflow_intent": "milk_analysis",
                "maternal_symptoms": {"fever": False, "breast_redness": False, "lump_or_hard_area": False},
            },
            {"user_message": "我没有发热红肿硬块", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertEqual(result["status"], "needs_clinical_context")
        self.assertEqual(result["data"]["missing_fields"], ["infant_signals"])
        self.assertIn("尿布", " ".join(result["data"]["suggested_questions"]))
        self.assertNotIn("乳房", " ".join(result["data"]["suggested_questions"]))
        compact = model_tool_output({"ok": True, "tool_name": "milk_assessment_evaluate", "result": result})
        self.assertEqual(compact["需要继续确认"]["还需要确认"], ["宝宝近 24 小时尿布、精神和吃奶表现"])

    def test_comprehensive_assessment_intent_forces_seven_complete_days(self) -> None:
        analysis_data = {
            "window": {
                "start_at": "2026-05-07 00:00:00",
                "end_at": "2026-05-14 00:00:00",
                "window_days": 7,
                "include_today": False,
            },
            "pumping_summary": {"count": 5, "total_ml": 520},
            "calendar_task_summary": {"days": 7},
            "milk_normality": {"overall_status": "normal", "stats": {"valid_days": 7}, "days": []},
            "assessment_status": "normal",
        }
        with patch(
            "momcozy_agent.tool_handlers.milk_management.evaluate_milk_status",
            return_value={"ok": True, "status": "milk_status_evaluated", "summary": "ok", "data": analysis_data},
        ) as evaluate:
            result = execute_milk_management_tool(
                {
                    "_tool_name": "milk_assessment_evaluate",
                    "user_id": "u1",
                    "as_of_time": "2026-05-14 12:00:00",
                    "window_days": 1,
                    "include_today": True,
                    "infant_signals": {"wet_diapers_24h": 6, "baby_state": "正常"},
                    "maternal_symptoms": {"fever": False, "breast_redness": False},
                },
                {"user_message": "分析最近吸奶情况", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
            )

        evaluate.assert_called_once_with(
            user_id="u1",
            as_of_time="2026-05-14 12:00:00",
            window_days=7,
            include_today=False,
        )
        self.assertNotIn("card", result)
        self.assertEqual(result["data"]["window"]["window_days"], 7)
        self.assertFalse(result["data"]["window"]["include_today"])

    def test_assessment_tool_does_not_force_comprehensive_window_for_plan_intent(self) -> None:
        uid, _ = _seed_user("assessment-card-suppressed")
        _add_pumping_rows(uid, "2026-05-13", ["06:00", "09:00", "12:00", "18:00", "21:00"])

        result = execute_milk_management_tool(
            {
                "_tool_name": "milk_assessment_evaluate",
                "user_id": uid,
                "as_of_time": "2026-05-14 12:00:00",
                "window_days": 1,
                "include_today": False,
            },
            {"user_message": "帮我生成追奶计划", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertTrue(result["ok"])
        self.assertNotIn("card", result)

    def test_plan_preview_prefers_calendar_pump_schedule_over_measured_records(self) -> None:
        uid, _ = _seed_user("plan-calendar-schedule")
        times = ["00:00", "03:00", "06:00", "09:00", "12:00", "15:00", "18:00", "21:00"]
        for offset, day in enumerate(["2026-05-07", "2026-05-08", "2026-05-09", "2026-05-10", "2026-05-11", "2026-05-12", "2026-05-13"]):
            for index, time in enumerate(times, start=1):
                _add_task(
                    uid,
                    task_id=offset * 10 + index,
                    content="吸奶",
                    item_type="吸奶",
                    is_milk_pump=1,
                    target_date=day,
                    start_time=time,
                    finish="true",
                )
            _add_pumping_rows(uid, day, ["06:00", "09:00", "12:00", "18:00", "21:00"])

        result = preview_milk_plan(
            user_id=uid,
            plan_type="increase_milk",
            plan_days=7,
            as_of_time="2026-05-14 12:00:00",
            options={
                "prepared_growth_assessment": {"status": "normal"},
                "observed_persistent_abnormal": True,
            },
        )

        self.assertTrue(result["ok"])
        draft = result["data"]["draft"]
        self.assertEqual(draft["observation_context"]["calendar_pump_tasks_per_day"], 8)
        self.assertEqual(draft["observation_context"]["recorded_pumping_logs_per_day"], 5)
        self.assertEqual(draft["generation_context"]["calendar_pump_times"], times)
        self.assertEqual(draft["schedule_basis"]["source"], "recent_calendar_schedule")
        self.assertEqual(draft["schedule_basis"]["confidence"], "high")
        self.assertFalse(draft["schedule_basis"]["ask_daily_counts"])

    def test_plan_preview_uses_recent_records_when_previous_day_is_empty(self) -> None:
        uid, infant_id = _seed_user("plan-recent-records")
        pumping_times = ["06:30", "10:30", "14:30", "18:30", "22:30"]
        breastfeeding_times = ["03:00", "08:00"]
        _add_pumping_rows(uid, "2026-05-12", pumping_times)
        _add_feeding_rows(uid, infant_id, "2026-05-12", breastfeeding_times)

        result = execute_milk_management_tool(
            {
                "_tool_name": "milk_plan_preview",
                "user_id": uid,
                "plan_type": "increase_milk",
                "plan_days": 7,
                "as_of_time": "2026-05-14 12:00:00",
                "options": {
                    "prepared_assessment": {
                        "pumping_summary": {"count": 35, "total_ml": 2450},
                        "feeding_summary": {"count": 14, "type_counts": {"亲喂": 14}},
                        "window": {"window_days": 7},
                        "milk_normality": {
                            "overall_status": "under_supply_alert",
                            "stats": {"valid_days": 7, "low_days": 5},
                            "days": [
                                {
                                    "ok": True,
                                    "date": "2026-05-12",
                                    "status": "low",
                                    "estimated_daily_milk_ml": 620,
                                    "yield_reference": {"p15": 700, "p85": 900},
                                }
                            ],
                        },
                    },
                    "prepared_growth_assessment": {"status": "normal"},
                    "observed_persistent_abnormal": True,
                    "infant_signals": {"wet_diapers_24h": 6, "baby_state": "正常"},
                    "maternal_symptoms": {"fever": False, "breast_redness": False, "lump_or_hard_area": False},
                },
            },
            {"user_message": "好的，帮我生成追奶计划", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertTrue(result["ok"])
        draft = result["data"]["draft"]
        basis = draft["schedule_basis"]
        self.assertEqual(basis["source"], "recent_records")
        self.assertEqual(basis["confidence"], "medium")
        self.assertEqual(basis["used_days"], ["2026-05-12"])
        self.assertEqual(basis["pumping_count_per_day"], len(pumping_times))
        self.assertEqual(basis["breastfeeding_count_per_day"], len(breastfeeding_times))
        self.assertFalse(basis["ask_daily_counts"])
        self.assertEqual(draft["generation_context"]["schedule_basis"]["used_days"], ["2026-05-12"])
        self.assertEqual(draft["generation_context"]["pumping_times"], pumping_times)
        self.assertEqual(draft["generation_context"]["breastfeeding_times"], breastfeeding_times)

        compact = model_tool_output({"ok": True, "tool_name": "milk_plan_preview", "result": result})
        plan_context = compact["plan_preview"]["plan_context"]
        self.assertTrue(any("不需要再向用户确认每天吸奶或亲喂次数" in item for item in plan_context["计划依据"]))
        self.assertIn("不要再问用户每天吸奶几次或亲喂几次", compact["final_response_instruction"])

    def test_plan_preview_tool_returns_structured_plan_card(self) -> None:
        uid, _ = _seed_user("plan-card")
        times = ["00:00", "03:00", "06:00", "09:00", "12:00", "15:00", "18:00", "21:00"]
        for offset, day in enumerate(["2026-05-07", "2026-05-08", "2026-05-09", "2026-05-10", "2026-05-11", "2026-05-12", "2026-05-13"]):
            for index, time in enumerate(times, start=1):
                _add_task(
                    uid,
                    task_id=offset * 10 + index,
                    content="吸奶",
                    item_type="吸奶",
                    is_milk_pump=1,
                    target_date=day,
                    start_time=time,
                    finish="true",
                )
            _add_pumping_rows(uid, day, ["06:00", "09:00", "12:00", "18:00", "21:00"])

        result = execute_milk_management_tool(
            {
                "_tool_name": "milk_plan_preview",
                "user_id": uid,
                "plan_type": "increase_milk",
                "plan_days": 7,
                "as_of_time": "2026-05-14 12:00:00",
                "options": {
                    "prepared_growth_assessment": {"status": "normal"},
                    "observed_persistent_abnormal": True,
                    "infant_signals": {"wet_diapers_24h": 6, "baby_state": "正常"},
                    "maternal_symptoms": {"fever": False, "breast_redness": False, "lump_or_hard_area": False},
                },
            },
            {"user_message": "", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["card"]["card_type"], "milk_plan_card")
        self.assertIn("assistant_followup", result)
        self.assertIn("同步到计划页", result["assistant_followup"]["message"])
        self.assertNotIn("硬扛", result["assistant_followup"]["message"])
        self.assertNotIn("移乳", result["assistant_followup"]["message"])
        self.assertNotIn("有效移出", result["assistant_followup"]["message"])
        card_json = result["card"]["card_json"]
        self.assertEqual(card_json["title"], "追奶计划")
        self.assertEqual(card_json["status_label"], "待确认")
        self.assertNotIn("subtitle", card_json)
        self.assertNotIn("headline", card_json)
        section_titles = [section["title"] for section in card_json["sections"]]
        self.assertEqual(section_titles[0], "目标")
        self.assertEqual(section_titles[1], "计划")
        self.assertEqual(section_titles[2], "每次怎么做")
        self.assertNotIn("安排", section_titles)
        self.assertNotIn("同步到日历", section_titles)
        self.assertIn("当前每日奶量", " ".join(card_json["sections"][0]["items"]))
        self.assertNotIn("当前参考日奶量", " ".join(card_json["sections"][0]["items"]))
        plan_metrics = card_json["sections"][1]["metrics"]
        plan_metric_labels = [metric["label"] for metric in plan_metrics]
        self.assertIn("原方案", plan_metric_labels)
        self.assertIn("新方案", plan_metric_labels)
        self.assertNotIn("原节奏", plan_metric_labels)
        self.assertNotIn("计划节奏", plan_metric_labels)
        self.assertTrue(all("次/天" in metric["value"] for metric in plan_metrics if metric["label"] in {"原方案", "新方案"}))
        self.assertTrue(all(metric["detail"] == "吸奶任务" for metric in plan_metrics if metric["label"] in {"原方案", "新方案"}))
        self.assertIn("保留原有", " ".join(card_json["sections"][1]["items"]))
        how_items = " ".join(card_json["sections"][2]["items"])
        self.assertIn("1-2 分钟", how_items)
        self.assertIn("2-3 天", how_items)
        compact = model_tool_output({"ok": True, "tool_name": "milk_plan_preview", "result": result})
        self.assertEqual(compact["card"]["card_type"], "milk_plan_card")
        self.assertTrue(compact["card"]["created"])
        self.assertIn("根据 plan_context 自然组织语言", compact["final_response_instruction"])
        self.assertNotIn("草稿", compact["final_response_instruction"])
        self.assertIn("calendar_sync_prompt", compact)
        self.assertIn("同步到日历", compact["next_actions"])
        self.assertNotIn("assistant_followup", compact)
        self.assertNotIn("assistant_followup.message", compact["final_response_instruction"])
        self.assertNotIn("summary", compact)
        self.assertIn("confirmed_plan_for_save", compact["plan_preview"])
        self.assertIn("plan_context", compact["plan_preview"])
        plan_context = compact["plan_preview"]["plan_context"]
        self.assertIn("温和追奶", plan_context["计划类型"])
        self.assertTrue(any("多数天低于参考区间" in item for item in plan_context["为什么建议这个方向"]))
        self.assertTrue(any("奶流明显变慢" in item for item in plan_context["每次吸奶或亲喂"]))
        self.assertTrue(any("2-3 天" in item for item in plan_context["复盘方式"]))
        compact_text = json.dumps(compact, ensure_ascii=False)
        self.assertNotIn("硬扛", compact_text)
        self.assertNotIn("移乳", compact_text)
        self.assertNotIn("有效移出", compact_text)
        self.assertNotIn("耗到很久", compact_text)

        confirmed = execute_milk_management_tool(
            {
                "_tool_name": "milk_plan_mutate",
                "user_id": uid,
                "operation": "create",
                "confirmed_plan": result["data"]["draft"],
                "idempotency_key": "plan-card-confirmed",
            },
            {"user_message": "", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )
        self.assertTrue(confirmed["ok"])
        self.assertIn("assistant_followup", confirmed)
        self.assertIn("同步到了日程", confirmed["assistant_followup"]["message"])
        self.assertIn("定时提醒", confirmed["assistant_followup"]["message"])
        self.assertIn("执行一段时间", confirmed["assistant_followup"]["message"])
        self.assertEqual(confirmed["card"]["id"], result["card"]["id"])
        self.assertEqual(confirmed["card"]["card_json"]["status_label"], "已确认")
        safe_confirmed = safe_tool_result({"ok": True, "tool_name": "milk_plan_mutate", "result": confirmed})
        self.assertEqual(safe_confirmed["card"]["card_type"], "milk_plan_card")
        self.assertIn("assistant_followup", safe_confirmed)
        compact_confirmed = model_tool_output({"ok": True, "tool_name": "milk_plan_mutate", "result": confirmed})
        self.assertNotIn("assistant_followup", compact_confirmed)
        self.assertNotIn("status", compact_confirmed)
        self.assertIn("user_context", compact_confirmed)
        self.assertIn("同步到日程", compact_confirmed["final_response_instruction"])
        self.assertIn("执行一段时间", compact_confirmed["final_response_instruction"])
        self.assertEqual(
            artifact_events_from_tool_result(
                tool_call_id="call_confirm",
                tool_call_name="milk_plan_mutate",
                safe_result=safe_confirmed,
            ),
            [],
        )

    def test_clinical_assessment_blocks_plan_when_maternal_red_flags_exist(self) -> None:
        uid, _ = _seed_user("clinical-red-flags")
        prepared_assessment = {
            "pumping_summary": {"count": 8, "total_ml": 520},
            "feeding_summary": {"type_counts": {}},
            "window": {"window_days": 1},
            "milk_normality": {
                "overall_status": "under_supply_alert",
                "stats": {"valid_days": 1},
                "days": [
                    {
                        "ok": True,
                        "date": "2026-05-13",
                        "status": "low",
                        "estimated_daily_milk_ml": 520,
                        "yield_reference": {"p15": 720, "p85": 950},
                    }
                ],
            },
        }

        result = evaluate_lactation_clinical_status(
            user_id=uid,
            as_of_time="2026-05-14 12:00:00",
            window_days=1,
            include_today=False,
            milk_assessment=prepared_assessment,
            growth_assessment={"status": "normal"},
            maternal_symptoms={"fever": True, "breast_redness": True, "worsening_pain": True},
            infant_signals={},
            requested_plan_type="increase_milk",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["risk_level"], "medical_recommended")
        self.assertFalse(result["data"]["plan_gate"]["allowed"])
        self.assertIn("ABM_MASTITIS_PROTOCOL", [item["id"] for item in result["data"]["evidence"]])

    def test_clinical_assessment_allows_plan_when_fullness_has_no_red_flags(self) -> None:
        uid, _ = _seed_user("clinical-fullness-no-red-flags")
        prepared_assessment = {
            "pumping_summary": {"count": 8, "total_ml": 520},
            "feeding_summary": {"type_counts": {}},
            "window": {"window_days": 1},
            "milk_normality": {
                "overall_status": "under_supply_alert",
                "stats": {"valid_days": 1},
                "days": [
                    {
                        "ok": True,
                        "date": "2026-05-13",
                        "status": "low",
                        "estimated_daily_milk_ml": 520,
                        "yield_reference": {"p15": 720, "p85": 950},
                    }
                ],
            },
        }

        result = evaluate_lactation_clinical_status(
            user_id=uid,
            as_of_time="2026-05-14 12:00:00",
            window_days=1,
            include_today=False,
            milk_assessment=prepared_assessment,
            growth_assessment={"status": "normal"},
            maternal_symptoms={
                "breast_fullness": True,
                "incomplete_emptying": True,
                "fever": False,
                "breast_redness": False,
                "lump_or_hard_area": False,
                "worsening_pain": False,
            },
            infant_signals={"wet_diapers_24h": 6, "baby_state": "正常"},
            requested_plan_type="increase_milk",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["domains"]["maternal_breast_symptoms"]["status"], "reassuring")
        self.assertTrue(result["data"]["domains"]["maternal_breast_symptoms"]["fullness_without_red_flags"])
        self.assertTrue(result["data"]["plan_gate"]["allowed"])
        self.assertIn("胀/排不空作为计划约束", " ".join(result["data"]["next_actions"]))

    def test_plan_preview_tool_uses_clinical_gate_before_generating_plan(self) -> None:
        uid, _ = _seed_user("plan-clinical-gate")
        result = execute_milk_management_tool(
            {
                "_tool_name": "milk_plan_preview",
                "user_id": uid,
                "plan_type": "increase_milk",
                "plan_days": 7,
                "as_of_time": "2026-05-14 12:00:00",
                "options": {
                    "maternal_symptoms": {"fever": True, "breast_redness": True, "worsening_pain": True},
                    "prepared_assessment": {
                        "pumping_summary": {"count": 8, "total_ml": 520},
                        "feeding_summary": {"type_counts": {}},
                        "window": {"window_days": 1},
                        "milk_normality": {
                            "overall_status": "under_supply_alert",
                            "stats": {"valid_days": 1},
                            "days": [
                                {
                                    "ok": True,
                                    "date": "2026-05-13",
                                    "status": "low",
                                    "estimated_daily_milk_ml": 520,
                                    "yield_reference": {"p15": 720, "p85": 950},
                                }
                            ],
                        },
                    },
                    "prepared_growth_assessment": {"status": "normal"},
                },
            },
            {"user_message": "帮我生成追奶计划", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "milk_plan_clinical_gate_blocked")
        self.assertIn("assistant_followup", result)
        self.assertIn("先不急着做", result["assistant_followup"]["message"])
        self.assertNotIn("card", result)
        clinical = result["data"]["clinical_assessment"]
        self.assertEqual(clinical["risk_level"], "medical_recommended")
        self.assertFalse(clinical["plan_gate"]["allowed"])

    def test_plan_preview_requires_missing_clinical_context_before_generating_plan(self) -> None:
        uid, _ = _seed_user("plan-missing-clinical-context")
        result = execute_milk_management_tool(
            {
                "_tool_name": "milk_plan_preview",
                "user_id": uid,
                "plan_type": "increase_milk",
                "plan_days": 7,
                "as_of_time": "2026-05-14 12:00:00",
                "options": {
                    "prepared_assessment": {
                        "pumping_summary": {"count": 8, "total_ml": 520},
                        "feeding_summary": {"type_counts": {}},
                        "window": {"window_days": 1},
                        "milk_normality": {
                            "overall_status": "under_supply_alert",
                            "stats": {"valid_days": 1},
                            "days": [
                                {
                                    "ok": True,
                                    "date": "2026-05-13",
                                    "status": "low",
                                    "estimated_daily_milk_ml": 520,
                                    "yield_reference": {"p15": 720, "p85": 950},
                                }
                            ],
                        },
                    },
                    "prepared_growth_assessment": {"status": "normal"},
                    "maternal_symptoms": {"fever": False, "breast_redness": False, "lump_or_hard_area": False},
                },
            },
            {"user_message": "帮我生成追奶计划", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "milk_plan_needs_clinical_context")
        self.assertEqual(result["data"]["workflow_intent"], "milk_plan_preview")
        self.assertIn("不是计划失败", result["data"]["continuation_instruction"])
        self.assertIn("再调用 milk_plan_preview", result["data"]["continuation_instruction"])
        self.assertEqual(result["data"]["missing_fields"], ["infant_signals"])
        self.assertNotIn("card", result)
        compact = model_tool_output({"ok": False, "tool_name": "milk_plan_preview", "result": result})
        self.assertNotIn("status", compact)
        self.assertNotIn("workflow_intent", compact)
        self.assertNotIn("continuation_instruction", compact)
        self.assertNotIn("assistant_followup", compact)
        self.assertIn("尿布", " ".join(compact["user_context"]["需要继续确认"]["可以这样问用户"]))
        self.assertIn("继续推进", compact["final_response_instruction"])
        self.assertIn("不要提工具", compact["final_response_instruction"])

    def test_plan_preview_keeps_fullness_without_red_flags_in_milk_plan_flow(self) -> None:
        uid, _ = _seed_user("plan-fullness-no-red-flags")
        result = execute_milk_management_tool(
            {
                "_tool_name": "milk_plan_preview",
                "user_id": uid,
                "plan_type": "increase_milk",
                "plan_days": 7,
                "as_of_time": "2026-05-14 12:00:00",
                "options": {
                    "maternal_symptoms": {
                        "breast_fullness": True,
                        "incomplete_emptying": True,
                        "fever": False,
                        "breast_redness": False,
                        "lump_or_hard_area": False,
                        "worsening_pain": False,
                    },
                    "infant_signals": {"wet_diapers_24h": 6, "baby_state": "正常"},
                    "prepared_assessment": {
                        "pumping_summary": {"count": 8, "total_ml": 520},
                        "feeding_summary": {"type_counts": {}},
                        "window": {"window_days": 1},
                        "milk_normality": {
                            "overall_status": "under_supply_alert",
                            "stats": {"valid_days": 1},
                            "days": [
                                {
                                    "ok": True,
                                    "date": "2026-05-13",
                                    "status": "low",
                                    "estimated_daily_milk_ml": 520,
                                    "yield_reference": {"p15": 720, "p85": 950},
                                }
                            ],
                        },
                    },
                    "prepared_growth_assessment": {"status": "normal"},
                },
            },
            {"user_message": "记录完整，我想做追奶计划，吸完还胀但没有发热红肿硬块", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "plan_preview_ready")
        self.assertIn("card", result)
        draft = result["data"]["draft"]
        self.assertTrue(draft["plan_rules"]["breast_fullness_without_red_flags"])
        self.assertIn("奶量偏低和吸完还胀一起看", draft["control_strategy"]["why_this_way"])
        self.assertNotIn("猛加", draft["control_strategy"]["why_this_way"])
        self.assertNotIn("有效移出", draft["control_strategy"]["session_goal"])
        self.assertIn("胀或感觉没排空", " ".join(draft["rule_notes"]))

    def test_milk_analysis_preset_copy_matches_status_and_trend_conditions(self) -> None:
        self.assertTrue(_milk_analysis_headline("under_supply_alert").startswith("最近整体偏低。"))
        self.assertTrue(_milk_analysis_headline("over_supply_alert").startswith("最近整体偏高。"))
        self.assertNotIn("压力", _milk_analysis_headline("under_supply_alert"))
        self.assertIn("不要突然减吸", _milk_analysis_headline("over_supply_alert"))
        next_step = _milk_next_step("under_supply_alert")
        self.assertIn("没记进来的吸奶", next_step)
        self.assertIn("结合宝宝和妈妈状态", next_step)
        self.assertNotIn("追奶计划", next_step)

        self.assertIn("记录还少", _milk_trend_text([]))
        self.assertIn("往下走", _milk_trend_text([{"estimated_daily_milk_ml": 700}, {"estimated_daily_milk_ml": 600}]))
        self.assertIn("往上走", _milk_trend_text([{"estimated_daily_milk_ml": 600}, {"estimated_daily_milk_ml": 700}]))
        self.assertIn("一路下降或上升", _milk_trend_text([{"estimated_daily_milk_ml": 650}, {"estimated_daily_milk_ml": 680}]))

    def test_plan_create_requires_strategy_when_future_plan_tasks_exist(self) -> None:
        uid, _ = _seed_user("plan-strategy-required")
        _seed_saved_plan_calendar(uid, task_count=3)
        plan = _simple_maintain_plan(plan_days=2)

        result = apply_milk_plan(
            user_id=uid,
            confirmed_plan=plan,
            idempotency_key="strategy-required",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "calendar_write_strategy_required")
        self.assertEqual(result["data"]["calendar_delta"]["existing_future_plan_task_count"], 3)

    def test_plan_create_can_append_or_replace_future_plan_tasks(self) -> None:
        append_uid, _ = _seed_user("plan-strategy-append")
        _seed_saved_plan_calendar(append_uid, task_count=3)
        plan = _simple_maintain_plan(plan_days=2)

        appended = apply_milk_plan(
            user_id=append_uid,
            confirmed_plan=plan,
            idempotency_key="strategy-append",
            calendar_write_strategy="append",
        )

        self.assertTrue(appended["ok"])
        self.assertEqual(appended["data"]["inserted_calendar_count"], 2)
        self.assertEqual(appended["data"]["replaced_calendar_count"], 0)
        self.assertEqual(_future_plan_task_count(append_uid, plan_days=2), 5)

        replace_uid, _ = _seed_user("plan-strategy-replace")
        _seed_saved_plan_calendar(replace_uid, task_count=3)
        replaced = apply_milk_plan(
            user_id=replace_uid,
            confirmed_plan=plan,
            idempotency_key="strategy-replace",
            calendar_write_strategy="replace_future_plan_tasks",
        )

        self.assertTrue(replaced["ok"])
        self.assertEqual(replaced["data"]["inserted_calendar_count"], 2)
        self.assertEqual(replaced["data"]["replaced_calendar_count"], 3)
        self.assertEqual(_future_plan_task_count(replace_uid, plan_days=2), 2)

    def test_plan_create_starts_calendar_tomorrow(self) -> None:
        uid, _ = _seed_user("plan-starts-tomorrow")
        plan = _simple_maintain_plan(plan_days=2)

        result = apply_milk_plan(
            user_id=uid,
            confirmed_plan=plan,
            idempotency_key="plan-starts-tomorrow",
        )

        self.assertTrue(result["ok"])
        tomorrow = datetime.now().date() + timedelta(days=1)
        self.assertEqual(result["data"]["calendar_delta"]["date_range"]["start_date"], tomorrow.isoformat())
        rows = _calendar_dates(uid)
        self.assertEqual(rows, [tomorrow.isoformat(), (tomorrow + timedelta(days=1)).isoformat()])

    def test_calendar_adjustment_apply_is_idempotent_for_same_key(self) -> None:
        uid, _ = _seed_user("calendar-adjustment-idempotent")
        today = _today()
        preview = preview_calendar_adjustment(
            user_id=uid,
            target_date=today,
            event_start_time="13:00",
            event_end_time="13:30",
            duration_minutes=None,
            content="临时吸奶",
            item_type="吸奶",
            plan_id=None,
        )
        proposal = preview["data"]["proposal"]

        first = apply_calendar_adjustment(user_id=uid, target_date=today, proposal=proposal, idempotency_key="same-calendar-key")
        second = apply_calendar_adjustment(user_id=uid, target_date=today, proposal=proposal, idempotency_key="same-calendar-key")

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(second["status"], "calendar_adjustment_idempotent_replay")
        self.assertEqual(_scalar("SELECT COUNT(*) FROM calendar WHERE user_id = ? AND source = '用户输入'", (uid,)), 1)

    def test_calendar_adjustment_preview_can_use_image_extracted_custom_event(self) -> None:
        uid, _ = _seed_user("calendar-adjustment-image")
        item_id = _add_task(uid, task_id=1, content="吸奶", item_type="吸奶", is_milk_pump=1)

        preview = preview_calendar_adjustment(
            user_id=uid,
            target_date="2026-05-14",
            event_start_time="09:00",
            event_end_time="10:00",
            duration_minutes=None,
            content="产检",
            item_type="自定义",
            plan_id=None,
        )

        self.assertTrue(preview["ok"])
        self.assertEqual(preview["data"]["insert_event"]["type"], "自定义")
        self.assertEqual(preview["data"]["conflict_count"], 1)
        self.assertEqual(preview["data"]["updates"][0]["item_id"], item_id)
        self.assertEqual(preview["data"]["updates"][0]["new_start_time"], "2026-05-14 10:00:00")

    def test_calendar_reschedule_preview_and_apply_uses_busy_windows(self) -> None:
        uid, _ = _seed_user("calendar-reschedule")
        item_id = _add_task(uid, task_id=1, content="吸奶", item_type="吸奶", is_milk_pump=1, start_time="09:00")
        _add_task(uid, task_id=2, content="吸奶", item_type="吸奶", is_milk_pump=1, start_time="13:00")

        preview = preview_day_reschedule(
            user_id=uid,
            target_date="2026-05-14",
            busy_windows=json.dumps([{"start_time": "09:00", "end_time": "10:30", "content": "团队会议"}], ensure_ascii=False),
            adjustable_item_types=json.dumps(["吸奶"], ensure_ascii=False),
            plan_id=None,
            default_duration_minutes=20,
            min_gap_minutes=90,
            include_busy_events=True,
        )

        self.assertTrue(preview["ok"])
        self.assertEqual(preview["data"]["conflict_count"], 1)
        self.assertEqual(preview["data"]["updates"][0]["item_id"], item_id)
        self.assertEqual(preview["data"]["updates"][0]["new_start_time"], "2026-05-14 08:40:00")

        applied = apply_calendar_reschedule(
            user_id=uid,
            target_date="2026-05-14",
            proposal=preview["data"]["proposal"],
            idempotency_key="calendar-reschedule-key",
        )
        replay = apply_calendar_reschedule(
            user_id=uid,
            target_date="2026-05-14",
            proposal=preview["data"]["proposal"],
            idempotency_key="calendar-reschedule-key",
        )

        self.assertTrue(applied["ok"])
        self.assertEqual(applied["data"]["applied_updates"][0]["item_id"], item_id)
        self.assertEqual(_scalar("SELECT COUNT(*) FROM calendar WHERE user_id = ? AND type = '自定义'", (uid,)), 1)
        self.assertEqual(_scalar_text("SELECT start_time FROM calendar WHERE user_id = ? AND item_id = ?", (uid, item_id)), "2026-05-14 08:40:00")
        self.assertEqual(replay["status"], "calendar_reschedule_idempotent_replay")
        self.assertEqual(_scalar("SELECT COUNT(*) FROM calendar WHERE user_id = ? AND type = '自定义'", (uid,)), 1)


def _seed_user(user_id: str) -> tuple[str, int]:
    with transaction() as conn:
        for table in ("user_profile", "infant_profile", "calendar", "feeding_log", "pumping_log", "infant_growth_log"):
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        conn.execute(
            "INSERT INTO user_profile(user_id, user_nickname, delivery_date) VALUES (?, 'Test User', '2026-04-14')",
            (user_id,),
        )
        cursor = conn.execute(
            """
            INSERT INTO infant_profile(user_id, user_nickname, infant_name, sex, birth_date)
            VALUES (?, 'Test User', 'Baby', 'female', '2026-04-14')
            """,
            (user_id,),
        )
        return user_id, int(cursor.lastrowid or 0)


def _add_task(
    user_id: str,
    *,
    task_id: int,
    content: str,
    item_type: str,
    is_milk_pump: int,
    target_date: str = "2026-05-14",
    start_time: str = "09:00",
    duration_minutes: int = 20,
    finish: str = "false",
) -> int:
    start_at = datetime.fromisoformat(f"{target_date} {start_time}:00")
    end_at = start_at + timedelta(minutes=duration_minutes)
    with transaction() as conn:
        cursor = conn.execute(
            """
            INSERT INTO calendar(user_id, date, task_id, start_time, end_time, content, type, source, is_milk_pump, finish)
            VALUES (?, ?, ?, ?, ?, ?, ?, '系统生成', ?, ?)
            """,
            (
                user_id,
                target_date,
                int(task_id),
                start_at.strftime("%Y-%m-%d %H:%M:%S"),
                end_at.strftime("%Y-%m-%d %H:%M:%S"),
                content,
                item_type,
                int(is_milk_pump),
                finish,
            ),
        )
        return int(cursor.lastrowid or 0)


def _add_pumping_rows(user_id: str, target_date: str, times: list[str]) -> None:
    with transaction() as conn:
        for time in times:
            conn.execute(
                """
                INSERT INTO pumping_log(
                    user_id, pump_start_time, pump_end_time, pump_milk_volum,
                    pump_type, pump_milk_duration, pump_source, pump_title
                )
                VALUES (?, ?, ?, 70, 0, 15, 1, '吸奶')
                """,
                (user_id, f"{target_date} {time}:00", f"{target_date} {time}:00"),
            )


def _add_feeding_rows(user_id: str, infant_id: int, target_date: str, times: list[str]) -> None:
    with transaction() as conn:
        for time in times:
            conn.execute(
                """
                INSERT INTO feeding_log(
                    user_id, infant_id, feed_time, feed_type, feed_milk_volum,
                    feed_action, feeding_title
                )
                VALUES (?, ?, ?, '亲喂', 0, 0, '亲喂')
                """,
                (user_id, infant_id, f"{target_date} {time}:00"),
            )


def _simple_maintain_plan(*, plan_days: int) -> dict[str, Any]:
    return {
        "plan_type": "maintain_milk",
        "plan_name": "稳奶计划",
        "plan_days": plan_days,
        "summary": "维持当前节奏。",
        "current_daily_ml": 600,
        "target_daily_ml": 600,
        "plan_rules": {"desired_pumping_count": 1},
        "daily_schedule_templates": [
            {
                "day_start": 1,
                "day_end": plan_days,
                "items": [
                    {
                        "time": "09:00",
                        "calendar_title": "吸奶",
                        "action": "维持当前节奏吸奶",
                        "duration_minutes": 30,
                    }
                ],
            }
        ],
    }


def _seed_saved_plan_calendar(user_id: str, *, task_count: int) -> None:
    start = datetime.now().date() + timedelta(days=1)
    with transaction() as conn:
        cursor = conn.execute(
            """
            INSERT INTO milk_plan(user_id, plan_name, plan_type, plan_days, plan_summary)
            VALUES (?, '旧计划', 'maintain_milk', 2, '旧计划')
            """,
            (user_id,),
        )
        plan_id = int(cursor.lastrowid or 0)
        for index in range(task_count):
            target_date = (start + timedelta(days=index % 2)).isoformat()
            hour = 8 + index
            conn.execute(
                """
                INSERT INTO calendar(user_id, plan_id, date, task_id, start_time, end_time, content, type, source, is_milk_pump, finish)
                VALUES (?, ?, ?, ?, ?, ?, '吸奶', '吸奶', '系统生成', 1, 'false')
                """,
                (
                    user_id,
                    plan_id,
                    target_date,
                    index + 1,
                    f"{target_date} {hour:02d}:00:00",
                    f"{target_date} {hour:02d}:30:00",
                ),
            )


def _future_plan_task_count(user_id: str, *, plan_days: int) -> int:
    start = datetime.now().date() + timedelta(days=1)
    end = start + timedelta(days=plan_days)
    return _scalar(
        """
        SELECT COUNT(*)
        FROM calendar
        WHERE user_id = ?
          AND date >= ?
          AND date < ?
          AND plan_id IS NOT NULL
          AND type IN ('吸奶', '亲喂')
          AND finish != 'true'
        """,
        (user_id, start.isoformat(), end.isoformat()),
    )


def _calendar_dates(user_id: str) -> list[str]:
    rows = fetch_all(
        """
        SELECT date
        FROM calendar
        WHERE user_id = ?
          AND plan_id IS NOT NULL
        ORDER BY date ASC, start_time ASC
        """,
        (user_id,),
    )
    return [str(row["date"]) for row in rows]


def _today() -> str:
    return datetime.now().date().isoformat()


def _max_linear_gap_minutes(times: list[str]) -> int:
    minutes = sorted({int(time[:2]) * 60 + int(time[3:5]) for time in times})
    return max((right - left for left, right in zip(minutes, minutes[1:])), default=0)


def _night_task_count(times: list[str]) -> int:
    return sum(1 for time in times if _is_night_time_text(time))


def _is_night_time_text(time: str) -> bool:
    minute = int(time[:2]) * 60 + int(time[3:5])
    return minute < 6 * 60 or minute >= 22 * 60


def _scalar(sql: str, params: tuple[Any, ...]) -> int:
    with transaction() as conn:
        row = conn.execute(sql, params).fetchone()
        return int(row[0] or 0)


def _scalar_text(sql: str, params: tuple[Any, ...]) -> str:
    with transaction() as conn:
        row = conn.execute(sql, params).fetchone()
        return str(row[0] or "") if row else ""


if __name__ == "__main__":
    unittest.main()
