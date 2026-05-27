from __future__ import annotations

import os
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from typing import Any

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
from momcozy_agent.agents import model_tool_output
from momcozy_agent.tool_handlers.milk_management import execute_milk_management_tool


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

    def test_assessment_tool_returns_structured_analysis_card(self) -> None:
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
                "window_days": 1,
                "include_today": False,
            },
            {"user_message": "分析最近吸奶情况", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertEqual(result["card"]["card_type"], "milk_analysis_card")
        card_json = result["card"]["card_json"]
        self.assertEqual(card_json["sections"][0]["title"], "数据统计")
        self.assertNotIn("数据口径", [section["title"] for section in card_json["sections"]])
        metric_labels = [metric["label"] for metric in card_json["sections"][0]["metrics"]]
        self.assertIn("记录与补录", metric_labels)
        self.assertIn("参考区间", metric_labels)
        self.assertNotIn("参考下沿", metric_labels)
        compact = model_tool_output({"ok": True, "tool_name": "milk_assessment_evaluate", "result": result})
        self.assertEqual(compact["card"]["card_type"], "milk_analysis_card")
        self.assertTrue(compact["card"]["created"])
        self.assertIn("不要重复卡片", compact["final_response_instruction"])
        self.assertNotIn("data", compact)

    def test_assessment_tool_suppresses_analysis_card_for_plan_intent(self) -> None:
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
                "options": {"prepared_growth_assessment": {"status": "normal"}, "observed_persistent_abnormal": True},
            },
            {"user_message": "", "locale": "zh-CN", "timezone": "Asia/Shanghai", "message_sent_at": ""},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["card"]["card_type"], "milk_plan_card")
        self.assertEqual(result["card"]["card_json"]["sections"][0]["title"], "计划方向")
        compact = model_tool_output({"ok": True, "tool_name": "milk_plan_preview", "result": result})
        self.assertEqual(compact["card"]["card_type"], "milk_plan_card")
        self.assertTrue(compact["card"]["created"])
        self.assertIn("不要重复卡片", compact["final_response_instruction"])
        self.assertIn("confirmed_plan_for_save", compact["plan_preview"])

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
