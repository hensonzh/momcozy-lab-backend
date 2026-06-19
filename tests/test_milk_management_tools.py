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
from momcozy_agent.services.milk_management.db import fetch_all, transaction
from momcozy_agent.services.milk_management.growth_mutation import mutate_infant_growth
from momcozy_agent.services.milk_management.assessment import evaluate_milk_status
from momcozy_agent.services.milk_management.plan import apply_milk_plan, preview_milk_plan, validate_milk_plan
from momcozy_agent.services.milk_management.status import query_milk_status
from momcozy_agent.services.milk_management.task_completion import complete_milk_task
from momcozy_agent.agents import _should_disable_tools_after_tool_results, artifact_events_from_tool_result, model_tool_output, safe_tool_result
from momcozy_agent.contexts import ContextState, build_request_context, record_milk_management_tool_state
from momcozy_agent.tool_handlers.milk_management import (
    _milk_analysis_headline,
    _milk_next_step,
    _milk_trend_text,
    execute_milk_management_tool,
)


def _reassuring_infant_signals() -> dict[str, Any]:
    return {
        "wet_diapers_24h": 6,
        "baby_state": "精神状态正常",
        "feeding_satisfaction": "吃奶后能安稳一会儿",
        "recent_weight": "体重增长正常",
    }


def _reassuring_maternal_symptoms() -> dict[str, Any]:
    return {
        "fever": False,
        "chills": False,
        "breast_redness": False,
        "lump_or_hard_area": False,
        "worsening_pain": False,
        "breast_fullness": False,
        "incomplete_emptying": False,
        "symptom_text": "吸奶后乳房舒适",
    }


def _complete_milk_analysis_context(
    *,
    plan_type: str | None = "increase_milk",
    infant_signals: dict[str, Any] | None = None,
    maternal_symptoms: dict[str, Any] | None = None,
    assessment_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = assessment_data.get("source_record_context") if isinstance(assessment_data, dict) and isinstance(assessment_data.get("source_record_context"), dict) else {}
    normality = assessment_data.get("milk_normality") if isinstance(assessment_data, dict) and isinstance(assessment_data.get("milk_normality"), dict) else {}
    stats = normality.get("stats") if isinstance(normality.get("stats"), dict) else {}
    return {
        "records_snapshot": {
            "status": "collected",
            "valid_days": stats.get("valid_days", 7),
            "positive_days": stats.get("valid_days", 7),
            "record_counts": source.get("record_counts") if isinstance(source.get("record_counts"), dict) else {},
            "daily_rollups": source.get("daily_rollups") if isinstance(source.get("daily_rollups"), list) else [],
            "raw_records": source.get("raw_records") if isinstance(source.get("raw_records"), dict) else {},
        },
        "source_records": {
            "daily_rollups": source.get("daily_rollups") if isinstance(source.get("daily_rollups"), list) else [],
            "raw_records": source.get("raw_records") if isinstance(source.get("raw_records"), dict) else {},
        },
        "infant_signals": infant_signals or _reassuring_infant_signals(),
        "maternal_symptoms": maternal_symptoms or _reassuring_maternal_symptoms(),
        "checklist": [
            {"id": "records_7d", "status": "collected"},
            {"id": "infant_wet_diapers", "status": "collected"},
            {"id": "infant_state_or_satisfaction", "status": "collected"},
            {"id": "infant_growth_signal", "status": "collected"},
            {"id": "maternal_red_flags", "status": "collected"},
            {"id": "maternal_breast_comfort", "status": "collected"},
        ],
        "plan_type": plan_type,
    }


def _assessment_result(assessment_data: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "status": assessment_data.get("assessment_status") or "milk_assessment_ready", "data": assessment_data}


def _analysis_context_from_tool_args(arguments: dict[str, Any]) -> dict[str, Any]:
    infant_signals = arguments.get("infant_signals") if isinstance(arguments.get("infant_signals"), dict) else {}
    maternal_symptoms = arguments.get("maternal_symptoms") if isinstance(arguments.get("maternal_symptoms"), dict) else {}
    checklist = [
        {"id": "records_7d", "status": "collected"},
        {"id": "infant_wet_diapers", "status": "collected" if infant_signals.get("wet_diapers_24h") not in (None, "", [], {}) else "missing"},
        {
            "id": "infant_state_or_satisfaction",
            "status": "collected"
            if any(infant_signals.get(key) not in (None, "", [], {}) for key in ("baby_state", "feeding_satisfaction", "poor_feeding", "poor_latch", "lethargy"))
            else "missing",
        },
        {
            "id": "infant_growth_signal",
            "status": "collected"
            if any(infant_signals.get(key) not in (None, "", [], {}) for key in ("recent_weight", "weight_trend", "growth_concern"))
            else "missing",
        },
        {
            "id": "maternal_red_flags",
            "status": "collected"
            if any(key in maternal_symptoms for key in ("fever", "chills", "breast_redness", "lump_or_hard_area", "worsening_pain"))
            else "missing",
        },
        {
            "id": "maternal_breast_comfort",
            "status": "collected"
            if any(key in maternal_symptoms for key in ("breast_fullness", "engorgement", "post_pump_fullness", "incomplete_emptying", "pain_level", "symptom_text"))
            else "missing",
        },
    ]
    return {
        "records_snapshot": {"status": "collected", "valid_days": 7, "positive_days": 7, "record_counts": {}},
        "source_records": {"daily_rollups": [], "raw_records": {}},
        "infant_signals": infant_signals,
        "maternal_symptoms": maternal_symptoms,
        "checklist": checklist,
        "plan_type": arguments.get("plan_type"),
    }


def _execute_milk_analysis_evaluate_result(arguments: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    args = dict(arguments)
    args["_tool_name"] = "milk_analysis_evaluate"
    args.setdefault("analysis_context", _analysis_context_from_tool_args(args))
    return execute_milk_management_tool(args, inputs)


def _milk_analysis_assessment_result(result: dict[str, Any]) -> dict[str, Any]:
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    return data.get("assessment_result") if isinstance(data.get("assessment_result"), dict) else {}


def _milk_analysis_assessment_data(result: dict[str, Any]) -> dict[str, Any]:
    assessment = _milk_analysis_assessment_result(result)
    return assessment.get("data") if isinstance(assessment.get("data"), dict) else {}


def _execute_milk_plan_preview_create(arguments: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    args = dict(arguments)
    args["_tool_name"] = "milk_plan_preview_create"
    options = args.get("options") if isinstance(args.get("options"), dict) else {}
    assessment_data = options.get("prepared_assessment") if isinstance(options.get("prepared_assessment"), dict) else None
    if assessment_data is None:
        assessment_result = evaluate_milk_status(
            user_id=args["user_id"],
            as_of_time=args.get("as_of_time"),
            window_days=7,
            include_today=False,
        )
        assessment_data = assessment_result.get("data") if isinstance(assessment_result.get("data"), dict) else {}
    infant_signals = options.get("infant_signals") if isinstance(options.get("infant_signals"), dict) else _reassuring_infant_signals()
    maternal_symptoms = options.get("maternal_symptoms") if isinstance(options.get("maternal_symptoms"), dict) else _reassuring_maternal_symptoms()
    args.setdefault(
        "analysis_context",
        _complete_milk_analysis_context(
            plan_type=args.get("plan_type"),
            infant_signals=infant_signals,
            maternal_symptoms=maternal_symptoms,
            assessment_data=assessment_data,
        ),
    )
    args.setdefault("assessment_result", _assessment_result(assessment_data))
    return execute_milk_management_tool(args, inputs)


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

        result = _execute_milk_analysis_evaluate_result(
            {
                "_tool_name": "milk_analysis_evaluate",
                "user_id": uid,
                "as_of_time": "2026-05-14 12:00:00",
                "window_days": 7,
                "include_today": False,
                "infant_signals": _reassuring_infant_signals(),
                "maternal_symptoms": _reassuring_maternal_symptoms(),
            },
            {"user_message": "分析最近吸奶情况", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )
        assessment = _milk_analysis_assessment_result(result)
        assessment_data = _milk_analysis_assessment_data(result)

        self.assertNotIn("card", result)
        self.assertIn("data", result)
        self.assertIn("clinical_assessment", assessment_data)
        self.assertIn("recent_milk_rhythm", assessment_data)
        self.assertIn("source_record_context", assessment_data)
        self.assertEqual(assessment_data["recent_milk_rhythm"]["selected_day"]["date"], "2026-05-13")
        self.assertEqual(assessment_data["source_record_context"]["record_counts"]["pumping"], 5)
        self.assertTrue(assessment_data["source_record_context"]["raw_records"]["pumping"])
        safe = safe_tool_result({"ok": True, "tool_name": "milk_analysis_evaluate", "result": result})
        self.assertNotIn("source_record_context", json.dumps(safe, ensure_ascii=False))
        self.assertNotIn("raw_records", json.dumps(safe, ensure_ascii=False))
        compact = model_tool_output({"ok": True, "tool_name": "milk_analysis_evaluate", "result": result})
        self.assertNotIn("card", compact)
        self.assertIn("facts", compact)
        self.assertIn("interpretation", compact)
        self.assertIn("workflow", compact)
        self.assertNotIn("response_instruction", compact)
        self.assertNotIn("status", compact)
        self.assertNotIn("assistant_followup", compact)
        reply_facts = compact["facts"]
        reply_interpretation = compact["interpretation"]
        self.assertEqual(reply_interpretation["overall_status"], "under_supply_alert")
        self.assertEqual(reply_facts["record_counts"]["pumping"], 5)
        self.assertTrue(reply_facts["raw_records"]["pumping"])
        self.assertTrue(any(day["date"] == "2026-05-13" for day in reply_facts["daily_rollups"]))
        self.assertNotIn("recent_milk_rhythm", reply_facts)
        self.assertNotIn("control_suggestion", reply_interpretation)
        flow_decision = compact["milk_flow_decision"]
        self.assertTrue(flow_decision["现在能不能开始制定计划"])
        self.assertEqual(flow_decision["建议计划方向"], "追奶计划")
        self.assertEqual(flow_decision["下一步工具"], "milk_plan_preview_create")
        self.assertIn("近 7 天奶量产出偏低", flow_decision["原因"])
        self.assertNotIn("记录完整后的推荐承接", reply_interpretation)
        self.assertNotIn("接下来怎么调", reply_interpretation)
        self.assertIn("final_response_instruction", compact)
        self.assertIn("询问用户是否现在生成奶量计划", compact["final_response_instruction"])
        self.assertIn("信息采集已经结束", compact["final_response_instruction"])
        self.assertIn("不要继续追问任何新的诊断、排程或计划细节", compact["final_response_instruction"])
        self.assertIn("任何用户追问都必须来自 milk_analysis_intake_manage", compact["final_response_instruction"])
        compact_text = json.dumps(compact, ensure_ascii=False)
        self.assertNotIn("夜里或清晨", compact_text)
        self.assertNotIn("夜里/清晨", compact_text)
        self.assertNotIn("夜间或清晨", compact_text)
        self.assertNotIn("单次排空", compact_text)
        self.assertNotIn("单次奶阵", compact_text)
        self.assertNotIn("固定模板", json.dumps(compact, ensure_ascii=False))
        self.assertNotIn("工具建议话术", compact)
        self.assertTrue(assessment)
        json.dumps(result, ensure_ascii=False)

    def test_assessment_state_tells_next_confirmation_to_continue_plan_flow_without_reasking_records(self) -> None:
        uid, _ = _seed_user("assessment-state-plan")
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

        result = _execute_milk_analysis_evaluate_result(
            {
                "_tool_name": "milk_analysis_evaluate",
                "user_id": uid,
                "as_of_time": "2026-05-14 12:00:00",
                "window_days": 7,
                "include_today": False,
                "infant_signals": _reassuring_infant_signals(),
                "maternal_symptoms": _reassuring_maternal_symptoms(),
            },
            {"user_message": "分析最近吸奶情况", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )
        state = ContextState()
        record_milk_management_tool_state(state, "milk_analysis_evaluate", {"ok": True, "tool_name": "milk_analysis_evaluate", "result": result})

        context = build_request_context(
            {
                "user_message": "继续",
                "user_profile": {"user_id": uid},
                "locale": "zh-CN",
                "timezone": "Asia/Shanghai",
                "message_sent_at": "2026-05-14 12:10:00",
            },
            state,
        )

        self.assertIn("milk_management_context:", context)
        self.assertIn("milk_analysis_intake_stage: analysis_ready", context)
        self.assertIn("milk_analysis_result_available: true", context)
        self.assertIn("调用 milk_plan_preview_create", context)
        self.assertIn("奶量分析信息采集已结束", context)
        self.assertIn("任何用户追问都必须来自 milk_analysis_intake_manage", context)
        self.assertIn("不要询问工具已读取的 7 天记录或近期节奏", context)

    def test_plan_preview_reuses_previous_assessment_context_without_reasking_records(self) -> None:
        uid, _ = _seed_user("assessment-state-plan-preview")
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

        assessment = _execute_milk_analysis_evaluate_result(
            {
                "_tool_name": "milk_analysis_evaluate",
                "user_id": uid,
                "as_of_time": "2026-05-14 12:00:00",
                "window_days": 7,
                "include_today": False,
                "infant_signals": _reassuring_infant_signals(),
                "maternal_symptoms": _reassuring_maternal_symptoms(),
            },
            {"user_message": "分析最近吸奶情况", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )
        state = ContextState()
        record_milk_management_tool_state(state, "milk_analysis_evaluate", {"ok": True, "tool_name": "milk_analysis_evaluate", "result": assessment})
        json.dumps(state.milk_management_state, ensure_ascii=False)

        result = _execute_milk_plan_preview_create(
            {
                "_tool_name": "milk_plan_preview_create",
                "user_id": uid,
                "plan_type": "increase_milk",
                "plan_days": 7,
                "as_of_time": "2026-05-14 12:00:00",
            },
            {
                "user_message": "好的",
                "locale": "zh-CN",
                "timezone": "Asia/Shanghai",
                "message_sent_at": "",
                "_milk_management_state": state.milk_management_state,
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "plan_preview_ready")
        self.assertNotEqual(result["status"], "milk_plan_needs_clinical_context")
        draft = result["data"]["draft"]
        self.assertFalse(draft["schedule_basis"]["ask_daily_counts"])
        self.assertEqual(draft["schedule_basis"]["pumping_count_per_day"], 8)

    def test_analysis_intake_evaluate_preview_then_save_plan(self) -> None:
        uid, _ = _seed_user("analysis-intake-plan-preview")
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

        state = ContextState()

        first = execute_milk_management_tool(
            {
                "_tool_name": "milk_analysis_intake_manage",
                "user_id": uid,
                "action": "start",
                "user_update": "分析最近吸奶情况",
                "as_of_time": "2026-05-14 12:00:00",
            },
            {"user_message": "分析最近吸奶情况", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )
        self.assertTrue(first["ok"])
        self.assertEqual(first["status"], "milk_analysis_intake_collecting")
        self.assertEqual(first["data"]["executed_step"], "intake")
        self.assertEqual(first["data"]["current_field"], "infant_wet_diapers")
        self.assertIn("records_7d", [item["id"] for item in first["data"]["checklist"] if item["status"] == "collected"])
        first_compact = model_tool_output({"ok": True, "tool_name": "milk_analysis_intake_manage", "result": first})
        self.assertIn("final_response_instruction", first_compact)
        self.assertIn("当前只问", first_compact["final_response_instruction"])
        self.assertIn("尿量", first_compact["final_response_instruction"])
        self.assertNotIn("精神状态怎么样", first_compact["final_response_instruction"])
        record_milk_management_tool_state(state, "milk_analysis_intake_manage", {"ok": True, "tool_name": "milk_analysis_intake_manage", "result": first})

        answers = [
            ("宝宝近24小时尿量正常", "infant_state_or_satisfaction"),
            ("精神好，吃完能安稳一会儿", "infant_growth_signal"),
            ("体重增长正常", "maternal_red_flags"),
            ("没有发热寒战红肿硬块，疼痛也没有加重", "maternal_breast_comfort"),
            ("吸完比较舒服，乳房状态正常", None),
        ]
        latest = first
        for user_update, expected_next_field in answers:
            latest = execute_milk_management_tool(
                {
                    "_tool_name": "milk_analysis_intake_manage",
                    "user_id": uid,
                    "action": "update",
                    "user_update": user_update,
                    "as_of_time": "2026-05-14 12:00:00",
                },
                {
                    "user_message": user_update,
                    "locale": "zh-CN",
                    "timezone": "Asia/Shanghai",
                    "message_sent_at": "",
                    "_milk_management_state": state.milk_management_state,
                },
            )
            record_milk_management_tool_state(state, "milk_analysis_intake_manage", {"ok": True, "tool_name": "milk_analysis_intake_manage", "result": latest})
            if expected_next_field:
                self.assertEqual(latest["data"]["current_field"], expected_next_field)
        self.assertEqual(latest["status"], "milk_analysis_ready_to_evaluate")
        self.assertEqual(latest["data"]["executed_step"], "intake")
        self.assertIn("analysis_context", latest["data"])
        self.assertIn("raw_records", latest["data"]["analysis_context"]["source_records"])
        self.assertEqual(state.milk_management_state["analysis_intake"]["stage"], "ready_to_evaluate")
        self.assertFalse([item for item in latest["data"]["checklist"] if item["status"] != "collected"])

        intake_compact = model_tool_output({"ok": True, "tool_name": "milk_analysis_intake_manage", "result": latest})
        self.assertEqual(intake_compact["tool_name"], "milk_analysis_intake_manage")
        self.assertIn("analysis_context", intake_compact)
        self.assertNotIn("response_instruction", intake_compact)

        assessment = execute_milk_management_tool(
            {
                "_tool_name": "milk_analysis_evaluate",
                "user_id": uid,
                "analysis_context": latest["data"]["analysis_context"],
                "as_of_time": "2026-05-14 12:00:00",
            },
            {
                "user_message": "继续分析",
                "locale": "zh-CN",
                "timezone": "Asia/Shanghai",
                "message_sent_at": "",
                "_milk_management_state": state.milk_management_state,
            },
        )
        self.assertTrue(assessment["ok"])
        self.assertEqual(assessment["data"]["executed_step"], "assessment")
        self.assertIn("assessment_result", assessment["data"])
        self.assertEqual(assessment["data"]["next_tool"], "milk_plan_preview_create")
        record_milk_management_tool_state(state, "milk_analysis_evaluate", {"ok": True, "tool_name": "milk_analysis_evaluate", "result": assessment})
        self.assertEqual(state.milk_management_state["analysis_intake"]["stage"], "analysis_ready")

        result = execute_milk_management_tool(
            {
                "_tool_name": "milk_plan_preview_create",
                "user_id": uid,
                "analysis_context": latest["data"]["analysis_context"],
                "assessment_result": assessment["data"]["assessment_result"],
                "user_update": "先按每天多50ml来做",
                "plan_days": 7,
                "as_of_time": "2026-05-14 12:00:00",
            },
            {
                "user_message": "先按每天多50ml来做",
                "locale": "zh-CN",
                "timezone": "Asia/Shanghai",
                "message_sent_at": "",
                "_milk_management_state": state.milk_management_state,
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "plan_preview_ready")
        self.assertEqual(result["data"]["executed_step"], "plan_preview")
        self.assertIn("card", result)
        self.assertEqual(result["data"]["analysis_context"]["delta_ml"], 50.0)
        self.assertEqual(result["data"]["plan_preview"]["status"], "plan_preview_ready")
        compact = model_tool_output({"ok": True, "tool_name": "milk_plan_preview_create", "result": result})
        self.assertEqual(compact["tool_name"], "milk_plan_preview_create")
        self.assertIn("workflow", compact)
        self.assertNotIn("response_instruction", compact)

        record_milk_management_tool_state(state, "milk_plan_preview_create", {"ok": True, "tool_name": "milk_plan_preview_create", "result": result})
        self.assertEqual(state.milk_management_state["analysis_intake"]["stage"], "plan_preview")
        self.assertEqual(state.milk_management_state["analysis_intake"]["plan_preview"]["status"], "plan_preview_ready")

        saved = execute_milk_management_tool(
            {
                "_tool_name": "milk_plan_mutate",
                "user_id": uid,
                "operation": "create",
                "confirmed": True,
                "confirmed_plan": "{}",
                "patch": "{}",
                "reexpand_calendar": True,
                "delete_calendar_items": False,
                "calendar_write_strategy": None,
                "idempotency_key": "",
            },
            {
                "user_message": "保存并同步",
                "locale": "zh-CN",
                "timezone": "Asia/Shanghai",
                "message_sent_at": "",
                "_milk_management_state": state.milk_management_state,
            },
        )
        self.assertTrue(saved["ok"])
        self.assertEqual(saved["status"], "plan_applied")
        self.assertGreater(saved["data"]["inserted_calendar_count"], 0)

    def test_analysis_intake_absorbs_multiple_answers_in_one_turn(self) -> None:
        uid, _ = _seed_user("analysis-intake-multi-field-answer")
        for day in ["2026-05-07", "2026-05-08", "2026-05-09", "2026-05-10", "2026-05-11", "2026-05-12", "2026-05-13"]:
            _add_pumping_rows(uid, day, ["06:00", "09:00", "12:00", "18:00", "21:00"])

        first = execute_milk_management_tool(
            {
                "_tool_name": "milk_analysis_intake_manage",
                "user_id": uid,
                "action": "start",
                "user_update": "分析最近吸奶情况",
                "as_of_time": "2026-05-14 12:00:00",
            },
            {"user_message": "分析最近吸奶情况", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )
        state = ContextState()
        record_milk_management_tool_state(state, "milk_analysis_intake_manage", {"ok": True, "tool_name": "milk_analysis_intake_manage", "result": first})

        second = execute_milk_management_tool(
            {
                "_tool_name": "milk_analysis_intake_manage",
                "user_id": uid,
                "action": "update",
                "user_update": "宝宝近24小时尿布正常，精神好，吃完能安稳一会儿，体重增长正常",
                "as_of_time": "2026-05-14 12:00:00",
            },
            {
                "user_message": "宝宝近24小时尿布正常，精神好，吃完能安稳一会儿，体重增长正常",
                "locale": "zh-CN",
                "timezone": "Asia/Shanghai",
                "message_sent_at": "",
                "_milk_management_state": state.milk_management_state,
            },
        )

        self.assertEqual(second["status"], "milk_analysis_intake_collecting")
        self.assertEqual(second["data"]["current_field"], "maternal_red_flags")
        self.assertEqual(second["data"]["remaining_count"], 2)
        completed = [item["id"] for item in second["data"]["checklist"] if item["status"] == "collected"]
        self.assertIn("infant_wet_diapers", completed)
        self.assertIn("infant_state_or_satisfaction", completed)
        self.assertIn("infant_growth_signal", completed)

    def test_assessment_tool_requires_clinical_context_before_comprehensive_analysis(self) -> None:
        uid, _ = _seed_user("assessment-needs-clinical-context")
        _add_pumping_rows(uid, "2026-05-13", ["06:00", "09:00", "12:00", "18:00", "21:00"])

        result = _execute_milk_analysis_evaluate_result(
            {
                "_tool_name": "milk_analysis_evaluate",
                "user_id": uid,
                "as_of_time": "2026-05-14 12:00:00",
                "window_days": 7,
                "include_today": False,
            },
            {"user_message": "分析最近吸奶情况", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "milk_analysis_intake_collecting")
        self.assertIn("assistant_followup", result)
        self.assertIn("尿布", result["assistant_followup"]["message"])
        self.assertNotIn("乳房", result["assistant_followup"]["message"])
        self.assertNotIn("card", result)
        self.assertEqual(result["data"]["workflow_intent"], "milk_analysis")
        self.assertEqual(result["data"]["current_field"], "infant_wet_diapers")
        self.assertIn("尿布", result["data"]["next_question"])
        self.assertEqual(result["data"]["remaining_count"], 5)
        self.assertEqual(result["data"]["next_tool"], "milk_analysis_intake_manage")
        self.assertEqual(
            result["data"]["missing_fields"],
            [
                "infant_wet_diapers",
                "infant_state_or_satisfaction",
                "infant_growth_signal",
                "maternal_red_flags",
                "maternal_breast_comfort",
            ],
        )
        self.assertIn("补齐宝宝状态和妈妈乳房情况", result["summary"])
        compact = model_tool_output({"ok": True, "tool_name": "milk_analysis_evaluate", "result": result})
        self.assertNotIn("card", compact)
        self.assertEqual(compact["workflow"]["next_tool"], "milk_analysis_intake_manage")
        self.assertNotIn("assistant_followup", compact)
        self.assertEqual(
            compact["需要继续确认"]["还需要确认"],
            [
                "宝宝近 24 小时尿量/尿布情况",
                "宝宝精神状态和吃奶后表现",
                "宝宝近期体重增长情况",
                "妈妈有没有发热、寒战、红肿、硬块或疼痛加重",
                "吸奶或亲喂后乳房舒适度",
            ],
        )
        self.assertEqual(compact["需要继续确认"]["current_field"], "infant_wet_diapers")
        self.assertIn("尿布", compact["需要继续确认"]["current_question"])
        self.assertNotIn("可以这样问用户", compact["需要继续确认"])
        self.assertNotIn("工具建议话术", compact)
        self.assertIn("final_response_instruction", compact)
        self.assertIn("当前只问", compact["final_response_instruction"])
        self.assertNotIn("乳房是比较舒服", compact["final_response_instruction"])
        self.assertIn("不要说“最后一个”", compact["final_response_instruction"])
        json.dumps(result, ensure_ascii=False)

    def test_assessment_missing_context_state_forces_next_turn_to_continue_evaluation(self) -> None:
        uid, _ = _seed_user("assessment-state-needs-context")
        _add_pumping_rows(uid, "2026-05-13", ["06:00", "09:00", "12:00", "18:00", "21:00"])

        result = _execute_milk_analysis_evaluate_result(
            {
                "_tool_name": "milk_analysis_evaluate",
                "user_id": uid,
                "as_of_time": "2026-05-14 12:00:00",
                "window_days": 7,
                "include_today": False,
            },
            {"user_message": "分析最近吸奶情况", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )
        state = ContextState()
        record_milk_management_tool_state(state, "milk_analysis_evaluate", {"ok": True, "tool_name": "milk_analysis_evaluate", "result": result})

        context = build_request_context(
            {
                "user_message": "宝宝尿量正常",
                "user_profile": {"user_id": uid},
                "locale": "zh-CN",
                "timezone": "Asia/Shanghai",
                "message_sent_at": "2026-05-14 12:10:00",
            },
            state,
        )

        self.assertIn("milk_management_context:", context)
        self.assertIn("milk_analysis_intake_stage: intake_collecting", context)
        self.assertIn(
            "milk_analysis_intake_missing_fields: infant_wet_diapers, infant_state_or_satisfaction, infant_growth_signal, maternal_red_flags, maternal_breast_comfort",
            context,
        )
        self.assertIn("milk_analysis_intake_current_field: infant_wet_diapers", context)
        self.assertIn("milk_analysis_intake_next_question: 宝宝近 24 小时尿量或尿布情况大概怎么样？", context)
        self.assertIn("milk_analysis_required_tool: milk_analysis_intake_manage", context)
        self.assertIn("用户本轮若是在回答上一轮奶量分析追问", context)
        self.assertIn("必须调用 milk_analysis_intake_manage 继续采集", context)
        self.assertIn("不要直接分析或生成计划", context)
        self.assertIn("用户可见回复只追问 milk_analysis_intake_next_question", context)
        self.assertNotIn("milk_analysis_result_available: true", context)

    def test_assessment_tool_keeps_asking_when_only_infant_context_is_answered(self) -> None:
        uid, _ = _seed_user("assessment-missing-maternal-context")
        _add_pumping_rows(uid, "2026-05-13", ["06:00", "09:00", "12:00", "18:00", "21:00"])

        result = _execute_milk_analysis_evaluate_result(
            {
                "_tool_name": "milk_analysis_evaluate",
                "user_id": uid,
                "as_of_time": "2026-05-14 12:00:00",
                "window_days": 7,
                "include_today": False,
                "workflow_intent": "milk_analysis",
                "infant_signals": _reassuring_infant_signals(),
            },
            {"user_message": "宝宝尿布和精神都正常", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertEqual(result["status"], "milk_analysis_intake_collecting")
        self.assertEqual(result["data"]["missing_fields"], ["maternal_red_flags", "maternal_breast_comfort"])
        self.assertIn("乳房", " ".join(result["data"]["suggested_questions"]))
        self.assertNotIn("尿布", " ".join(result["data"]["suggested_questions"]))
        compact = model_tool_output({"ok": True, "tool_name": "milk_analysis_evaluate", "result": result})
        self.assertEqual(
            compact["需要继续确认"]["还需要确认"],
            ["妈妈有没有发热、寒战、红肿、硬块或疼痛加重", "吸奶或亲喂后乳房舒适度"],
        )

    def test_assessment_accepts_qualitative_wet_diaper_answer(self) -> None:
        uid, _ = _seed_user("assessment-qualitative-wet-diapers")
        _add_pumping_rows(uid, "2026-05-13", ["06:00", "09:00", "12:00", "18:00", "21:00"])

        result = _execute_milk_analysis_evaluate_result(
            {
                "_tool_name": "milk_analysis_evaluate",
                "user_id": uid,
                "as_of_time": "2026-05-14 12:00:00",
                "window_days": 7,
                "include_today": False,
                "workflow_intent": "milk_analysis",
                "infant_signals": {
                    "wet_diapers_24h": "和平时差不多",
                    "baby_state": "精神正常",
                    "feeding_satisfaction": "吃奶后能安稳一会儿",
                    "recent_weight": "体重增长正常",
                },
            },
            {"user_message": "尿布和平时差不多", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertEqual(result["status"], "milk_analysis_intake_collecting")
        self.assertEqual(result["data"]["missing_fields"], ["maternal_red_flags", "maternal_breast_comfort"])
        self.assertNotIn("尿布", " ".join(result["data"]["suggested_questions"]))

    def test_assessment_merges_previous_collected_context_between_turns(self) -> None:
        uid, _ = _seed_user("assessment-merge-previous-context")
        _add_pumping_rows(uid, "2026-05-13", ["06:00", "09:00", "12:00", "18:00", "21:00"])

        first = _execute_milk_analysis_evaluate_result(
            {
                "_tool_name": "milk_analysis_evaluate",
                "user_id": uid,
                "as_of_time": "2026-05-14 12:00:00",
                "window_days": 7,
                "include_today": False,
                "workflow_intent": "milk_analysis",
                "infant_signals": _reassuring_infant_signals(),
            },
            {"user_message": "宝宝尿布和精神都正常", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )
        state = ContextState()
        record_milk_management_tool_state(state, "milk_analysis_evaluate", {"ok": True, "tool_name": "milk_analysis_evaluate", "result": first})

        second = execute_milk_management_tool(
            {
                "_tool_name": "milk_analysis_intake_manage",
                "user_id": uid,
                "action": "update",
                "user_update": "没有发热、寒战、红肿、硬块或疼痛加重",
                "as_of_time": "2026-05-14 12:00:00",
                "maternal_symptoms": {
                    "fever": False,
                    "chills": False,
                    "breast_redness": False,
                    "lump_or_hard_area": False,
                    "worsening_pain": False,
                },
            },
            {
                "user_message": "没有发热、寒战、红肿、硬块或疼痛加重",
                "locale": "zh-CN",
                "timezone": "Asia/Shanghai",
                "message_sent_at": "",
                "_milk_management_state": state.milk_management_state,
            },
        )

        self.assertEqual(second["status"], "milk_analysis_intake_collecting")
        self.assertEqual(second["data"]["missing_fields"], ["maternal_breast_comfort"])
        self.assertNotIn("尿布", second["data"]["next_question"])

    def test_assessment_tool_keeps_asking_when_only_maternal_context_is_answered(self) -> None:
        uid, _ = _seed_user("assessment-missing-infant-context")
        _add_pumping_rows(uid, "2026-05-13", ["06:00", "09:00", "12:00", "18:00", "21:00"])

        result = _execute_milk_analysis_evaluate_result(
            {
                "_tool_name": "milk_analysis_evaluate",
                "user_id": uid,
                "as_of_time": "2026-05-14 12:00:00",
                "window_days": 7,
                "include_today": False,
                "workflow_intent": "milk_analysis",
                "maternal_symptoms": _reassuring_maternal_symptoms(),
            },
            {"user_message": "我没有发热红肿硬块", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertEqual(result["status"], "milk_analysis_intake_collecting")
        self.assertEqual(
            result["data"]["missing_fields"],
            ["infant_wet_diapers", "infant_state_or_satisfaction", "infant_growth_signal"],
        )
        self.assertIn("尿布", " ".join(result["data"]["suggested_questions"]))
        self.assertNotIn("乳房", " ".join(result["data"]["suggested_questions"]))
        compact = model_tool_output({"ok": True, "tool_name": "milk_analysis_evaluate", "result": result})
        self.assertEqual(
            compact["需要继续确认"]["还需要确认"],
            ["宝宝近 24 小时尿量/尿布情况", "宝宝精神状态和吃奶后表现", "宝宝近期体重增长情况"],
        )

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
            result = _execute_milk_analysis_evaluate_result(
                {
                    "_tool_name": "milk_analysis_evaluate",
                    "user_id": "u1",
                    "as_of_time": "2026-05-14 12:00:00",
                    "window_days": 1,
                    "include_today": True,
                    "infant_signals": _reassuring_infant_signals(),
                    "maternal_symptoms": _reassuring_maternal_symptoms(),
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
        assessment_data = _milk_analysis_assessment_data(result)
        self.assertEqual(assessment_data["window"]["window_days"], 7)
        self.assertFalse(assessment_data["window"]["include_today"])

    def test_assessment_tool_does_not_force_comprehensive_window_for_plan_intent(self) -> None:
        uid, _ = _seed_user("assessment-card-suppressed")
        _add_pumping_rows(uid, "2026-05-13", ["06:00", "09:00", "12:00", "18:00", "21:00"])

        result = _execute_milk_analysis_evaluate_result(
            {
                "_tool_name": "milk_analysis_evaluate",
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

        result = _execute_milk_plan_preview_create(
            {
                "_tool_name": "milk_plan_preview_create",
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
                    "infant_signals": _reassuring_infant_signals(),
                    "maternal_symptoms": _reassuring_maternal_symptoms(),
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
        rhythm = draft["generation_context"]["recent_milk_rhythm"]
        self.assertEqual(rhythm["summary"]["basis_date"], "2026-05-12")
        self.assertEqual(rhythm["selected_day"]["actual_nursing_times"], breastfeeding_times)
        self.assertEqual([item["time"] for item in rhythm["selected_day"]["actual_pumping"]], pumping_times)

        compact = model_tool_output({"ok": True, "tool_name": "milk_plan_preview_create", "result": result})
        self.assertNotIn("response_instruction", compact)
        plan_context = compact["plan_preview"]["plan_context"]
        self.assertTrue(any("不需要再向用户确认已知节奏信息" in item for item in plan_context["计划依据"]))
        self.assertTrue(any("不需要重复追问已知节奏信息" in item for item in plan_context["最近7天吸奶和亲喂节奏"]["摘要"]))
        self.assertTrue(any(day["日期"] == "2026-05-12" for day in plan_context["最近7天吸奶和亲喂节奏"]["每天明细"]))
        self.assertNotIn("final_response_instruction", compact)

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

        result = _execute_milk_plan_preview_create(
            {
                "_tool_name": "milk_plan_preview_create",
                "user_id": uid,
                "plan_type": "increase_milk",
                "plan_days": 7,
                "as_of_time": "2026-05-14 12:00:00",
                "options": {
                    "prepared_growth_assessment": {"status": "normal"},
                    "observed_persistent_abnormal": True,
                    "infant_signals": _reassuring_infant_signals(),
                    "maternal_symptoms": _reassuring_maternal_symptoms(),
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
        self.assertIn("现在", plan_metric_labels)
        self.assertIn("计划", plan_metric_labels)
        self.assertNotIn("原节奏", plan_metric_labels)
        self.assertNotIn("计划节奏", plan_metric_labels)
        self.assertTrue(all("次/天" in metric["value"] for metric in plan_metrics if metric["label"] in {"现在", "计划"}))
        self.assertTrue(all(metric["detail"] == "吸奶任务" for metric in plan_metrics if metric["label"] in {"现在", "计划"}))
        self.assertIn("保留原有", " ".join(card_json["sections"][1]["items"]))
        how_items = " ".join(card_json["sections"][2]["items"])
        self.assertIn("1～2分钟", how_items)
        self.assertIn("明显痛感", how_items)
        self.assertNotIn("2-3 天", how_items)
        compact = model_tool_output({"ok": True, "tool_name": "milk_plan_preview_create", "result": result})
        self.assertEqual(compact["card"]["card_type"], "milk_plan_card")
        self.assertTrue(compact["card"]["created"])
        self.assertNotIn("final_response_instruction", compact)
        self.assertIn("calendar_sync_prompt", compact)
        self.assertIn("同步到日历", compact["next_actions"])
        self.assertNotIn("assistant_followup", compact)
        self.assertNotIn("summary", compact)
        self.assertIn("facts", compact)
        self.assertIn("interpretation", compact)
        self.assertIn("workflow", compact)
        self.assertIn("raw_records", compact["facts"])
        self.assertTrue(compact["facts"]["daily_rollups"])
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
                "confirmed": True,
                "confirmed_plan": result["data"]["draft"],
                "idempotency_key": "plan-card-confirmed",
            },
            {"user_message": "确认同步这版计划", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )
        self.assertTrue(confirmed["ok"])
        self.assertIn("assistant_followup", confirmed)
        self.assertIn("同步到计划页", confirmed["assistant_followup"]["message"])
        self.assertIn("提醒", confirmed["assistant_followup"]["message"])
        self.assertIn("最近几天", confirmed["assistant_followup"]["message"])
        self.assertEqual(confirmed["card"]["id"], result["card"]["id"])
        self.assertEqual(confirmed["card"]["card_json"]["status_label"], "已确认")
        safe_confirmed = safe_tool_result({"ok": True, "tool_name": "milk_plan_mutate", "result": confirmed})
        self.assertEqual(safe_confirmed["card"]["card_type"], "milk_plan_card")
        self.assertNotIn("assistant_followup", safe_confirmed)
        self.assertEqual(safe_confirmed["plan_feedback"]["kind"], "milk_plan")
        self.assertEqual(safe_confirmed["plan_feedback"]["reason"], "synced")
        self.assertIn("calendar_dates", safe_confirmed)
        self.assertTrue(all(date.startswith("2026-") for date in safe_confirmed["calendar_dates"]))
        compact_confirmed = model_tool_output({"ok": True, "tool_name": "milk_plan_mutate", "result": confirmed})
        self.assertNotIn("assistant_followup", compact_confirmed)
        self.assertEqual(compact_confirmed["card"]["card_type"], "milk_plan_card")
        self.assertNotIn("status", compact_confirmed)
        self.assertIn("user_context", compact_confirmed)
        self.assertNotIn("final_response_instruction", compact_confirmed)
        self.assertEqual(
            artifact_events_from_tool_result(
                tool_call_id="call_confirm",
                tool_call_name="milk_plan_mutate",
                safe_result=safe_confirmed,
            ),
            [],
        )

    def test_plan_preview_uses_seven_day_average_current_daily_milk(self) -> None:
        uid, _ = _seed_user("plan-average-current-milk")
        daily_estimates = [540, 550, 560, 570, 580, 590, 900]
        result = _execute_milk_plan_preview_create(
            {
                "_tool_name": "milk_plan_preview_create",
                "user_id": uid,
                "plan_type": "increase_milk",
                "plan_days": 7,
                "as_of_time": "2026-05-14 12:00:00",
                "options": {
                    "prepared_assessment": {
                        "pumping_summary": {"count": 56, "total_ml": sum(daily_estimates)},
                        "feeding_summary": {"count": 0, "type_counts": {}},
                        "window": {"window_days": 7},
                        "milk_normality": {
                            "overall_status": "under_supply_alert",
                            "stats": {"valid_days": 7, "low_days": 6},
                            "days": [
                                {
                                    "ok": True,
                                    "date": f"2026-05-{day:02d}",
                                    "status": "low" if estimate < 600 else "normal",
                                    "estimated_daily_milk_ml": estimate,
                                    "yield_reference": {"p15": 720, "p85": 950},
                                }
                                for day, estimate in zip(range(7, 14), daily_estimates)
                            ],
                        },
                    },
                    "prepared_growth_assessment": {"status": "normal"},
                    "observed_persistent_abnormal": True,
                    "infant_signals": _reassuring_infant_signals(),
                    "maternal_symptoms": _reassuring_maternal_symptoms(),
                },
            },
            {"user_message": "帮我生成追奶计划", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertTrue(result["ok"])
        draft = result["data"]["draft"]
        self.assertEqual(draft["current_daily_ml"], 612.9)
        self.assertNotEqual(draft["current_daily_ml"], daily_estimates[-1])
        target_items = " ".join(result["card"]["card_json"]["sections"][0]["items"])
        self.assertIn("当前每日奶量约 612.9 ml", target_items)

    def test_plan_preview_uses_current_seven_day_window_without_legacy_assessment_state(self) -> None:
        uid, _ = _seed_user("plan-reload-seven-day-average")
        daily_counts = [6, 7, 8, 9, 10, 11, 12]
        for offset, count in enumerate(daily_counts, start=7):
            _add_pumping_rows(uid, f"2026-05-{offset:02d}", [f"{index * 2:02d}:00" for index in range(count)])

        result = _execute_milk_plan_preview_create(
            {
                "_tool_name": "milk_plan_preview_create",
                "user_id": uid,
                "plan_type": "increase_milk",
                "plan_days": 7,
                "as_of_time": "2026-05-14 12:00:00",
                "options": {
                    "prepared_growth_assessment": {"status": "normal"},
                    "observed_persistent_abnormal": True,
                    "infant_signals": _reassuring_infant_signals(),
                    "maternal_symptoms": _reassuring_maternal_symptoms(),
                },
            },
            {
                "user_message": "帮我生成追奶计划",
                "locale": "zh-CN",
                "timezone": "Asia/Shanghai",
                "message_sent_at": "",
            },
        )

        self.assertTrue(result["ok"])
        draft = result["data"]["draft"]
        self.assertEqual(draft["current_daily_ml"], 630.0)
        self.assertNotEqual(draft["current_daily_ml"], 900)

    def test_plan_preview_uses_breastmilk_bottle_average_when_pumping_missing(self) -> None:
        uid, infant_id = _seed_user("plan-breastmilk-bottle-average")
        daily_amounts = [480, 500, 520, 540, 560, 580, 600]
        for offset, amount in enumerate(daily_amounts, start=7):
            _add_breastmilk_bottle_rows(uid, infant_id, f"2026-05-{offset:02d}", [("09:00", amount)])

        result = _execute_milk_plan_preview_create(
            {
                "_tool_name": "milk_plan_preview_create",
                "user_id": uid,
                "plan_type": "increase_milk",
                "plan_days": 7,
                "as_of_time": "2026-05-14 12:00:00",
                "options": {
                    "prepared_growth_assessment": {"status": "normal"},
                    "observed_persistent_abnormal": True,
                    "infant_signals": _reassuring_infant_signals(),
                    "maternal_symptoms": _reassuring_maternal_symptoms(),
                },
            },
            {"user_message": "帮我生成追奶计划", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "plan_preview_ready")
        draft = result["data"]["draft"]
        self.assertEqual(draft["current_daily_ml"], 540.0)
        assessment = result["data"]["assessment"]
        days = assessment["milk_normality"]["days"]
        self.assertEqual(assessment["milk_normality"]["stats"]["valid_days"], 7)
        self.assertTrue(all(day["milk_basis_rule"] == "breastmilk_bottle_fallback_no_pumping" for day in days))
        target_items = " ".join(result["card"]["card_json"]["sections"][0]["items"])
        self.assertIn("当前每日奶量约 540 ml", target_items)

    def test_plan_preview_does_not_create_zero_to_fifty_target_card(self) -> None:
        uid, _ = _seed_user("plan-zero-current-milk")
        result = _execute_milk_plan_preview_create(
            {
                "_tool_name": "milk_plan_preview_create",
                "user_id": uid,
                "plan_type": "increase_milk",
                "plan_days": 7,
                "as_of_time": "2026-05-14 12:00:00",
                "options": {
                    "prepared_assessment": {
                        "pumping_summary": {"count": 0, "total_ml": 0},
                        "feeding_summary": {"count": 0, "type_counts": {}},
                        "window": {"window_days": 7},
                        "milk_normality": {
                            "overall_status": "under_supply_alert",
                            "stats": {"valid_days": 7, "low_days": 7},
                            "days": [
                                {
                                    "ok": True,
                                    "date": f"2026-05-{day:02d}",
                                    "status": "low",
                                    "estimated_daily_milk_ml": 0,
                                    "yield_reference": {"p15": 720, "p85": 950},
                                }
                                for day in range(7, 14)
                            ],
                        },
                    },
                    "prepared_growth_assessment": {"status": "normal"},
                    "observed_persistent_abnormal": True,
                    "infant_signals": _reassuring_infant_signals(),
                    "maternal_symptoms": _reassuring_maternal_symptoms(),
                },
            },
            {"user_message": "帮我生成追奶计划", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "milk_plan_needs_milk_records")
        self.assertFalse(result["data"]["requires_confirmation"])
        self.assertNotIn("draft", result["data"])
        self.assertNotIn("card", result)
        compact = model_tool_output({"ok": True, "tool_name": "milk_plan_preview_create", "result": result})
        self.assertIn("过去 7 天", compact["user_context"]["当前结果"])
        self.assertNotIn("card", compact)
        self.assertNotIn("plan_preview", compact)
        self.assertNotIn("target_daily_ml", json.dumps(compact.get("facts"), ensure_ascii=False))

    def test_plan_preview_does_not_ask_user_for_daily_counts_when_schedule_basis_is_low(self) -> None:
        uid, _ = _seed_user("plan-no-daily-count-followup")
        daily_estimates = [610, 620, 615, 625, 630, 620, 618]
        result = _execute_milk_plan_preview_create(
            {
                "_tool_name": "milk_plan_preview_create",
                "user_id": uid,
                "plan_type": "increase_milk",
                "plan_days": 7,
                "as_of_time": "2026-05-14 12:00:00",
                "options": {
                    "prepared_assessment": {
                        "pumping_summary": {"count": 0, "total_ml": 0},
                        "feeding_summary": {"count": 0, "type_counts": {}},
                        "window": {"window_days": 7},
                        "milk_normality": {
                            "overall_status": "under_supply_alert",
                            "stats": {"valid_days": 7, "low_days": 7},
                            "days": [
                                {
                                    "ok": True,
                                    "date": f"2026-05-{day:02d}",
                                    "status": "low",
                                    "estimated_daily_milk_ml": estimate,
                                    "yield_reference": {"p15": 720, "p85": 950},
                                }
                                for day, estimate in zip(range(7, 14), daily_estimates)
                            ],
                        },
                    },
                    "prepared_growth_assessment": {"status": "normal"},
                    "observed_persistent_abnormal": True,
                    "infant_signals": _reassuring_infant_signals(),
                    "maternal_symptoms": _reassuring_maternal_symptoms(),
                },
            },
            {"user_message": "帮我生成追奶计划", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "plan_preview_ready")
        self.assertFalse(result["data"]["draft"]["schedule_basis"]["ask_daily_counts"])
        compact = model_tool_output({"ok": True, "tool_name": "milk_plan_preview_create", "result": result})
        compact_text = json.dumps(compact, ensure_ascii=False)
        self.assertNotIn("每天几次", compact_text)
        self.assertNotIn("大概节奏", compact_text)
        self.assertNotIn("大概次数", compact_text)
        self.assertNotIn("final_response_instruction", compact)

    def test_plan_confirm_can_save_from_runtime_preview_context(self) -> None:
        uid, _ = _seed_user("plan-confirm-context")
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

        preview = _execute_milk_plan_preview_create(
            {
                "_tool_name": "milk_plan_preview_create",
                "user_id": uid,
                "plan_type": "increase_milk",
                "plan_days": 3,
                "as_of_time": "2026-05-14 12:00:00",
                "options": {
                    "prepared_growth_assessment": {"status": "normal"},
                    "observed_persistent_abnormal": True,
                    "infant_signals": _reassuring_infant_signals(),
                    "maternal_symptoms": _reassuring_maternal_symptoms(),
                },
            },
            {"user_message": "", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )
        state = ContextState()
        record_milk_management_tool_state(state, "milk_plan_preview_create", {"ok": True, "tool_name": "milk_plan_preview_create", "result": preview})
        context = build_request_context(
            {"user_message": "确认，把这版计划同步到日历提醒", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
            state,
        )

        self.assertIn("milk_plan_preview_ready_for_save: true", context)
        self.assertIn("调用 milk_plan_mutate 创建计划", context)

        confirmed = execute_milk_management_tool(
            {
                "_tool_name": "milk_plan_mutate",
                "user_id": uid,
                "operation": "create",
                "confirmed": True,
                "confirmed_plan": {},
                "idempotency_key": "",
            },
            {
                "user_message": "确认，把这版计划同步到日历提醒",
                "locale": "zh-CN",
                "timezone": "Asia/Shanghai",
                "message_sent_at": "",
                "_milk_management_state": state.milk_management_state,
            },
        )

        self.assertTrue(confirmed["ok"])
        self.assertEqual(confirmed["status"], "plan_applied")
        self.assertGreater(confirmed["data"]["inserted_calendar_count"], 0)
        self.assertIn("calendar_items", confirmed["data"])
        self.assertEqual(confirmed["card"]["card_type"], "milk_plan_card")
        self.assertEqual(confirmed["card"]["card_json"]["status_label"], "已确认")

    def test_plan_confirm_uses_cached_preview_even_when_model_passes_invalid_plan(self) -> None:
        uid, _ = _seed_user("plan-confirm-overrides-invalid-model-plan")
        for offset, day in enumerate(["2026-05-07", "2026-05-08", "2026-05-09", "2026-05-10", "2026-05-11", "2026-05-12", "2026-05-13"]):
            _add_pumping_rows(uid, day, ["06:00", "09:00", "12:00", "18:00", "21:00"])

        preview = _execute_milk_plan_preview_create(
            {
                "_tool_name": "milk_plan_preview_create",
                "user_id": uid,
                "plan_type": "increase_milk",
                "plan_days": 3,
                "as_of_time": "2026-05-14 12:00:00",
                "options": {
                    "prepared_growth_assessment": {"status": "normal"},
                    "observed_persistent_abnormal": True,
                    "infant_signals": _reassuring_infant_signals(),
                    "maternal_symptoms": _reassuring_maternal_symptoms(),
                },
            },
            {"user_message": "", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )
        self.assertEqual(preview["status"], "plan_preview_ready")
        state = ContextState()
        record_milk_management_tool_state(state, "milk_plan_preview_create", {"ok": True, "tool_name": "milk_plan_preview_create", "result": preview})

        invalid_model_plan = {
            "plan_type": "increase_milk",
            "plan_name": "模型重拼的无效追奶计划",
            "plan_days": 3,
            "current_daily_ml": 615,
            "target_daily_ml": 665,
            "plan_rules": {"current_pumping_count": 5, "desired_pumping_count": 5},
            "daily_schedule_templates": [
                {
                    "day_start": 1,
                    "day_end": 3,
                    "items": [{"time": time, "calendar_title": "吸奶"} for time in ["06:00", "09:00", "12:00", "18:00", "21:00"]],
                }
            ],
        }
        self.assertFalse(validate_milk_plan(user_id=uid, plan=invalid_model_plan)["data"]["valid"])

        confirmed = execute_milk_management_tool(
            {
                "_tool_name": "milk_plan_mutate",
                "user_id": uid,
                "operation": "create",
                "confirmed": True,
                "confirmed_plan": invalid_model_plan,
                "idempotency_key": "",
            },
            {
                "user_message": "确认，把这版计划同步到日历提醒",
                "locale": "zh-CN",
                "timezone": "Asia/Shanghai",
                "message_sent_at": "",
                "_milk_management_state": state.milk_management_state,
            },
        )

        self.assertTrue(confirmed["ok"])
        self.assertEqual(confirmed["status"], "plan_applied")
        saved_names = [
            row["plan_name"]
            for row in fetch_all("SELECT plan_name FROM milk_plan WHERE user_id = ?", (uid,))
        ]
        self.assertNotIn("模型重拼的无效追奶计划", saved_names)

    def test_milk_write_tool_requires_confirmed_flag_and_current_user_confirmation(self) -> None:
        uid, _ = _seed_user("write-confirmation-guard")

        result = execute_milk_management_tool(
            {
                "_tool_name": "milk_record_mutate",
                "user_id": uid,
                "operation": "create",
                "confirmed": False,
                "record_kind": "pumping",
                "record_id": None,
                "occurred_at": "2026-05-14 09:30:00",
                "amount_ml": 80,
                "duration_minutes": 18,
                "infant_id": None,
                "title": None,
                "patch": "{}",
                "idempotency_key": "write-confirmation-guard",
            },
            {"user_message": "帮我记录 80ml", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "needs_write_confirmation")
        self.assertTrue(result["data"]["requires_confirmation"])
        self.assertEqual(_scalar("SELECT COUNT(*) FROM pumping_log WHERE user_id = ?", (uid,)), 0)
        compact = model_tool_output({"ok": True, "tool_name": "milk_record_mutate", "result": result})
        self.assertEqual(compact["status"], "needs_write_confirmation")
        self.assertTrue(compact["requires_confirmation"])
        self.assertNotIn("final_response_instruction", compact)

        missing_current_confirmation = execute_milk_management_tool(
            {
                "_tool_name": "milk_record_mutate",
                "user_id": uid,
                "operation": "create",
                "confirmed": True,
                "record_kind": "pumping",
                "record_id": None,
                "occurred_at": "2026-05-14 10:30:00",
                "amount_ml": 90,
                "duration_minutes": 20,
                "infant_id": None,
                "title": None,
                "patch": "{}",
                "idempotency_key": "write-confirmation-guard-2",
            },
            {"user_message": "今天 90ml", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertFalse(missing_current_confirmation["ok"])
        self.assertEqual(missing_current_confirmation["status"], "needs_write_confirmation")
        self.assertEqual(_scalar("SELECT COUNT(*) FROM pumping_log WHERE user_id = ?", (uid,)), 0)

    def test_successful_milk_write_invalidates_cached_milk_context(self) -> None:
        state = ContextState()
        state.milk_management_state = {
            "analysis_intake": {"stage": "analysis_ready", "assessment_result": {"status": "old"}},
            "last_plan_preview": {"status": "plan_preview_ready", "draft": {"plan_type": "increase_milk"}},
        }

        record_milk_management_tool_state(
            state,
            "milk_record_mutate",
            {"ok": True, "tool_name": "milk_record_mutate", "result": {"ok": True, "status": "record_created"}},
        )

        self.assertNotIn("analysis_intake", state.milk_management_state)
        self.assertNotIn("last_plan_preview", state.milk_management_state)

    def test_plan_preview_tool_uses_clinical_gate_before_generating_plan(self) -> None:
        uid, _ = _seed_user("plan-clinical-gate")
        result = _execute_milk_plan_preview_create(
            {
                "_tool_name": "milk_plan_preview_create",
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
        result = _execute_milk_plan_preview_create(
            {
                "_tool_name": "milk_plan_preview_create",
                "user_id": uid,
                "plan_type": "increase_milk",
                "plan_days": 7,
                "as_of_time": "2026-05-14 12:00:00",
                "analysis_context": _analysis_context_from_tool_args(
                    {"plan_type": "increase_milk", "maternal_symptoms": _reassuring_maternal_symptoms()}
                ),
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
                    "maternal_symptoms": _reassuring_maternal_symptoms(),
                },
            },
            {"user_message": "帮我生成追奶计划", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "milk_plan_needs_clinical_context")
        self.assertEqual(result["data"]["workflow_intent"], "milk_plan_preview_create")
        self.assertIn("仍处于奶量计划生成流程", result["data"]["continuation_instruction"])
        self.assertIn("再调用 milk_plan_preview_create", result["data"]["continuation_instruction"])
        self.assertEqual(result["data"]["current_field"], "infant_wet_diapers")
        self.assertIn("尿布", result["data"]["next_question"])
        self.assertEqual(result["data"]["suggested_questions"], [result["data"]["next_question"]])
        self.assertEqual(
            result["data"]["missing_fields"],
            ["infant_wet_diapers", "infant_state_or_satisfaction", "infant_growth_signal"],
        )
        self.assertNotIn("card", result)
        compact = model_tool_output({"ok": False, "tool_name": "milk_plan_preview_create", "result": result})
        self.assertNotIn("status", compact)
        self.assertNotIn("workflow_intent", compact)
        self.assertNotIn("continuation_instruction", compact)
        self.assertNotIn("assistant_followup", compact)
        self.assertNotIn("response_instruction", compact)
        self.assertIn("final_response_instruction", compact)
        self.assertIn("当前只问", compact["final_response_instruction"])
        self.assertNotIn("精神状态怎么样", compact["final_response_instruction"])
        self.assertIn("宝宝近 24 小时尿量/尿布情况", compact["user_context"]["需要继续确认"]["还需要确认"])
        self.assertNotIn("可以这样问用户", compact["user_context"]["需要继续确认"])

    def test_plan_preview_missing_context_state_continues_plan_flow_next_turn(self) -> None:
        uid, _ = _seed_user("plan-missing-context-state")
        result = _execute_milk_plan_preview_create(
            {
                "_tool_name": "milk_plan_preview_create",
                "user_id": uid,
                "plan_type": "increase_milk",
                "plan_days": 7,
                "as_of_time": "2026-05-14 12:00:00",
                "analysis_context": _analysis_context_from_tool_args(
                    {"plan_type": "increase_milk", "maternal_symptoms": _reassuring_maternal_symptoms()}
                ),
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
                    "maternal_symptoms": _reassuring_maternal_symptoms(),
                },
            },
            {"user_message": "帮我生成追奶计划", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )
        state = ContextState()
        record_milk_management_tool_state(state, "milk_plan_preview_create", {"ok": False, "tool_name": "milk_plan_preview_create", "result": result})

        context = build_request_context(
            {
                "user_message": "宝宝尿量正常",
                "user_profile": {"user_id": uid},
                "locale": "zh-CN",
                "timezone": "Asia/Shanghai",
                "message_sent_at": "2026-05-14 12:10:00",
            },
            state,
        )

        self.assertIn("milk_management_context:", context)
        self.assertIn("milk_analysis_intake_stage: intake_collecting", context)
        self.assertIn("milk_analysis_intake_missing_fields: infant_wet_diapers, infant_state_or_satisfaction, infant_growth_signal", context)
        self.assertIn("milk_analysis_intake_current_field: infant_wet_diapers", context)
        self.assertIn("milk_analysis_intake_next_question: 宝宝近 24 小时尿量或尿布情况大概怎么样？", context)
        self.assertIn("milk_analysis_required_tool: milk_analysis_intake_manage", context)
        self.assertIn("不要直接分析或生成计划", context)
        self.assertIn("用户可见回复只追问 milk_analysis_intake_next_question", context)

    def test_plan_preview_keeps_fullness_without_red_flags_in_milk_plan_flow(self) -> None:
        uid, _ = _seed_user("plan-fullness-no-red-flags")
        result = _execute_milk_plan_preview_create(
            {
                "_tool_name": "milk_plan_preview_create",
                "user_id": uid,
                "plan_type": "increase_milk",
                "plan_days": 7,
                "as_of_time": "2026-05-14 12:00:00",
                "options": {
                    "maternal_symptoms": {
                        "breast_fullness": True,
                        "incomplete_emptying": True,
                        "fever": False,
                        "chills": False,
                        "breast_redness": False,
                        "lump_or_hard_area": False,
                        "worsening_pain": False,
                    },
                    "infant_signals": _reassuring_infant_signals(),
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
        self.assertNotIn("夜间或清晨", _milk_analysis_headline("under_supply_alert"))
        self.assertNotIn("单次排空", _milk_analysis_headline("under_supply_alert"))
        self.assertNotIn("间隔变化", _milk_analysis_headline("over_supply_alert"))
        self.assertIn("不要突然减吸", _milk_analysis_headline("over_supply_alert"))
        next_step = _milk_next_step("under_supply_alert")
        self.assertIn("综合奶量分析流程", next_step)
        self.assertIn("是否生成奶量计划", next_step)
        self.assertNotIn("没记进来的吸奶", next_step)
        self.assertNotIn("手挤", next_step)
        self.assertNotIn("其他吸奶器", next_step)
        self.assertNotIn("线下记录", next_step)
        self.assertNotIn("追奶计划", next_step)

        self.assertIn("记录还少", _milk_trend_text([]))
        down_trend = _milk_trend_text([{"estimated_daily_milk_ml": 700}, {"estimated_daily_milk_ml": 600}])
        self.assertIn("往下走", down_trend)
        self.assertNotIn("漏记", down_trend)
        self.assertNotIn("休息不足", down_trend)
        self.assertNotIn("排得很空", down_trend)
        self.assertIn("往上走", _milk_trend_text([{"estimated_daily_milk_ml": 600}, {"estimated_daily_milk_ml": 700}]))
        self.assertIn("一路下降或上升", _milk_trend_text([{"estimated_daily_milk_ml": 650}, {"estimated_daily_milk_ml": 680}]))

    def test_milk_missing_intake_guard_stops_followup_tool_chain(self) -> None:
        self.assertTrue(
            _should_disable_tools_after_tool_results(
                [
                    {
                        "tool_name": "milk_analysis_evaluate",
                        "result": {
                            "ok": True,
                            "status": "milk_analysis_intake_collecting",
                            "data": {
                                "missing_fields": ["maternal_breast_comfort"],
                                "next_tool": "milk_analysis_intake_manage",
                                "next_question": "吸奶或亲喂后乳房是比较舒服，还是还会胀、排不空或疼？",
                            },
                        },
                    }
                ]
            )
        )

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

    def test_plan_mutate_strategy_required_output_does_not_claim_saved(self) -> None:
        uid, _ = _seed_user("plan-strategy-required-output")
        _seed_saved_plan_calendar(uid, task_count=3)
        plan = _simple_maintain_plan(plan_days=2)

        result = execute_milk_management_tool(
            {
                "_tool_name": "milk_plan_mutate",
                "user_id": uid,
                "operation": "create",
                "confirmed": True,
                "confirmed_plan": plan,
                "idempotency_key": "strategy-required-output",
            },
            {"user_message": "确认保存这版计划", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "calendar_write_strategy_required")
        self.assertEqual(_scalar("SELECT COUNT(*) FROM milk_plan WHERE user_id = ? AND plan_name = '稳奶计划'", (uid,)), 0)

        safe = safe_tool_result({"ok": True, "tool_name": "milk_plan_mutate", "result": result})
        self.assertFalse(safe["result_ok"])
        self.assertEqual(safe["calendar_delta"]["existing_future_plan_task_count"], 3)

        compact = model_tool_output({"ok": True, "tool_name": "milk_plan_mutate", "result": result})
        compact_text = json.dumps(compact, ensure_ascii=False)
        self.assertFalse(compact["ok"])
        self.assertEqual(compact["status"], "calendar_write_strategy_required")
        self.assertNotIn("final_response_instruction", compact)
        self.assertIn("追加到现有日程", compact_text)
        self.assertIn("替换未来未完成", compact_text)
        self.assertNotIn("计划已经保存", compact_text)
        self.assertNotIn("已经同步到计划页", compact_text)
        self.assertNotIn("接下来会提醒", compact_text)

    def test_invalid_plan_preview_does_not_create_saveable_card_or_state(self) -> None:
        uid, _ = _seed_user("invalid-preview-no-card")
        invalid_preview = {
            "ok": True,
            "status": "plan_preview_needs_revision",
            "summary": "追奶计划修改后吸奶任务次数必须大于当前吸奶次数。",
            "data": {
                "draft": {
                    "plan_type": "increase_milk",
                    "plan_days": 7,
                    "current_daily_ml": 615,
                    "target_daily_ml": 665,
                    "current_frequency": 6,
                    "plan_rules": {"current_pumping_count": 5, "desired_pumping_count": 5},
                    "daily_schedule_templates": [
                        {
                            "day_start": 1,
                            "day_end": 7,
                            "items": [
                                {"time": "06:00", "calendar_title": "吸奶"},
                                {"time": "09:00", "calendar_title": "吸奶"},
                                {"time": "12:00", "calendar_title": "吸奶"},
                                {"time": "15:00", "calendar_title": "吸奶"},
                                {"time": "18:00", "calendar_title": "吸奶"},
                            ],
                        }
                    ],
                },
                "validation": {
                    "valid": False,
                    "violations": ["追奶计划修改后吸奶任务次数必须大于当前吸奶次数。"],
                },
                "intake_state": {"stage": "analysis_ready"},
            },
        }

        with patch("momcozy_agent.tool_handlers.milk_management._create_milk_plan_preview_core", return_value=invalid_preview):
            result = _execute_milk_plan_preview_create(
                {
                    "_tool_name": "milk_plan_preview_create",
                    "user_id": uid,
                    "plan_type": "increase_milk",
                    "plan_days": 7,
                },
                {"user_message": "生成计划", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
            )

        self.assertEqual(result["status"], "plan_preview_needs_revision")
        self.assertNotIn("card", result)
        self.assertNotIn("assistant_followup", result)

        compact = model_tool_output({"ok": True, "tool_name": "milk_plan_preview_create", "result": result})
        self.assertNotIn("card", compact)
        self.assertFalse(compact["workflow"].get("requires_confirmation", False))

        state = ContextState()
        record_milk_management_tool_state(state, "milk_plan_preview_create", {"ok": True, "tool_name": "milk_plan_preview_create", "result": result})
        intake = state.milk_management_state.get("analysis_intake", {})
        self.assertNotEqual(intake.get("stage"), "plan_preview")
        self.assertNotIn("plan_preview", intake)
        self.assertNotIn("last_plan_preview", state.milk_management_state)

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

    def test_calendar_query_by_date_returns_plan_context(self) -> None:
        uid, _ = _seed_user("calendar-by-date-plan-context")
        plan = _simple_maintain_plan(plan_days=2)
        applied = apply_milk_plan(
            user_id=uid,
            confirmed_plan=plan,
            idempotency_key="calendar-by-date-plan-context",
        )
        self.assertTrue(applied["ok"])
        plan_id = applied["data"]["plan_id"]
        tomorrow = (datetime.now().date() + timedelta(days=1)).isoformat()

        result = execute_milk_management_tool(
            {
                "_tool_name": "milk_calendar_query",
                "user_id": uid,
                "query_mode": "by_date",
                "target_date": tomorrow,
                "include_items": True,
                "limit": 20,
            },
            {"user_message": "明天的奶量计划是什么", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "calendar_day_loaded")
        self.assertEqual(result["data"]["target_date"], tomorrow)
        self.assertEqual(result["data"]["plan_context"]["plan_ids"], [plan_id])
        self.assertEqual(result["data"]["plan_context"]["primary_plan"]["plan_name"], "稳奶计划")
        self.assertEqual(result["data"]["plan_context"]["task_count_by_plan_id"][str(plan_id)], 1)

    def test_calendar_current_plan_looks_ahead_when_today_has_no_plan_tasks(self) -> None:
        uid, _ = _seed_user("calendar-current-plan-context")
        plan = _simple_maintain_plan(plan_days=2)
        applied = apply_milk_plan(
            user_id=uid,
            confirmed_plan=plan,
            idempotency_key="calendar-current-plan-context",
        )
        self.assertTrue(applied["ok"])
        plan_id = applied["data"]["plan_id"]
        today = datetime.now().date()
        tomorrow = today + timedelta(days=1)

        result = execute_milk_management_tool(
            {
                "_tool_name": "milk_calendar_query",
                "user_id": uid,
                "query_mode": "current_plan",
                "lookahead_days": 3,
            },
            {
                "user_message": "我当前的奶量计划是什么",
                "locale": "zh-CN",
                "timezone": "Asia/Shanghai",
                "message_sent_at": f"{today.isoformat()} 09:00:00",
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "calendar_current_plan_loaded")
        self.assertEqual(result["data"]["requested_date"], today.isoformat())
        self.assertEqual(result["data"]["target_date"], tomorrow.isoformat())
        self.assertTrue(result["data"]["used_lookahead"])
        self.assertEqual(result["data"]["plan_context"]["primary_plan_id"], plan_id)
        self.assertEqual(result["data"]["plan_context"]["primary_plan"]["plan_name"], "稳奶计划")

    def test_calendar_current_plan_infers_legacy_system_schedule_without_plan_id(self) -> None:
        uid, _ = _seed_user("calendar-current-plan-inferred")
        _add_task(uid, task_id=1, content="吸奶", item_type="吸奶", is_milk_pump=1)
        _add_task(uid, task_id=2, content="吸奶", item_type="吸奶", is_milk_pump=1, start_time="12:00")

        result = execute_milk_management_tool(
            {
                "_tool_name": "milk_calendar_query",
                "user_id": uid,
                "query_mode": "current_plan",
                "target_date": "2026-05-14",
                "lookahead_days": 3,
            },
            {
                "user_message": "我当前的奶量计划是什么",
                "locale": "zh-CN",
                "timezone": "Asia/Shanghai",
                "message_sent_at": "2026-05-14 09:00:00",
            },
        )

        plan_context = result["data"]["plan_context"]
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "calendar_current_plan_loaded")
        self.assertEqual(result["data"]["target_date"], "2026-05-14")
        self.assertEqual(plan_context["plan_ids"], [])
        self.assertTrue(plan_context["has_plan_tasks"])
        self.assertTrue(plan_context["inferred_from_calendar"])
        self.assertEqual(plan_context["task_count_without_plan_id"], 2)
        self.assertEqual(plan_context["primary_plan"]["plan_name"], "稳奶计划")
        self.assertEqual(plan_context["primary_plan"]["plan_type"], "maintain_milk")

    def test_calendar_current_plan_does_not_infer_user_input_task_as_plan(self) -> None:
        uid, _ = _seed_user("calendar-current-plan-user-input")
        _add_user_input_task(uid, target_date="2026-05-14")

        result = execute_milk_management_tool(
            {
                "_tool_name": "milk_calendar_query",
                "user_id": uid,
                "query_mode": "current_plan",
                "target_date": "2026-05-14",
                "lookahead_days": 3,
            },
            {
                "user_message": "我当前的奶量计划是什么",
                "locale": "zh-CN",
                "timezone": "Asia/Shanghai",
                "message_sent_at": "2026-05-14 09:00:00",
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "calendar_current_plan_not_found")
        self.assertFalse(result["data"]["plan_context"]["has_plan_tasks"])

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
        preview_safe = safe_tool_result({"ok": True, "tool_name": "milk_calendar_reschedule_preview", "result": preview})
        self.assertNotIn("plan_feedback", preview_safe)

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
        applied_safe = safe_tool_result({"ok": True, "tool_name": "milk_calendar_mutate", "result": applied})
        self.assertEqual(applied_safe["plan_feedback"]["kind"], "milk_plan")
        self.assertEqual(applied_safe["plan_feedback"]["reason"], "rescheduled")
        self.assertEqual(applied_safe["plan_feedback"]["dates"], ["2026-05-14"])
        self.assertEqual(applied_safe["calendar_dates"], ["2026-05-14"])
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


def _add_user_input_task(user_id: str, *, target_date: str) -> int:
    start_at = datetime.fromisoformat(f"{target_date} 09:00:00")
    end_at = start_at + timedelta(minutes=20)
    with transaction() as conn:
        cursor = conn.execute(
            """
            INSERT INTO calendar(user_id, date, task_id, start_time, end_time, content, type, source, is_milk_pump, finish)
            VALUES (?, ?, 1, ?, ?, '临时吸奶', '吸奶', '用户输入', 1, 'false')
            """,
            (
                user_id,
                target_date,
                start_at.strftime("%Y-%m-%d %H:%M:%S"),
                end_at.strftime("%Y-%m-%d %H:%M:%S"),
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


def _add_breastmilk_bottle_rows(user_id: str, infant_id: int, target_date: str, entries: list[tuple[str, float]]) -> None:
    with transaction() as conn:
        for time, amount in entries:
            conn.execute(
                """
                INSERT INTO feeding_log(
                    user_id, infant_id, feed_time, feed_type, feed_milk_volum,
                    feed_action, feeding_title
                )
                VALUES (?, ?, ?, '瓶喂母乳', ?, 0, '瓶喂母乳')
                """,
                (user_id, infant_id, f"{target_date} {time}:00", float(amount)),
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
