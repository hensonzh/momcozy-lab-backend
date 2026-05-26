from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from typing import Any

MILK_MANAGEMENT_TOOLS_DB_PATH = os.path.join(tempfile.mkdtemp(prefix="momcozy-agent-tests-"), "milk_management.db")
os.environ["MILK_DB_PATH"] = MILK_MANAGEMENT_TOOLS_DB_PATH

from momcozy_agent.services.milk_management.calendar import apply_calendar_adjustment, preview_calendar_adjustment
from momcozy_agent.services.milk_management.db import transaction
from momcozy_agent.services.milk_management.growth_mutation import mutate_infant_growth
from momcozy_agent.services.milk_management.plan import apply_milk_plan, preview_milk_plan, validate_milk_plan
from momcozy_agent.services.milk_management.status import query_milk_status
from momcozy_agent.services.milk_management.task_completion import complete_milk_task


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


def _add_task(user_id: str, *, task_id: int, content: str, item_type: str, is_milk_pump: int) -> int:
    with transaction() as conn:
        cursor = conn.execute(
            """
            INSERT INTO calendar(user_id, date, task_id, start_time, end_time, content, type, source, is_milk_pump, finish)
            VALUES (?, '2026-05-14', ?, '2026-05-14 09:00:00', '2026-05-14 09:20:00', ?, ?, '系统生成', ?, 'false')
            """,
            (user_id, int(task_id), content, item_type, int(is_milk_pump)),
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
    start = datetime.now().date()
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
    start = datetime.now().date()
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


def _today() -> str:
    return datetime.now().date().isoformat()


def _max_linear_gap_minutes(times: list[str]) -> int:
    minutes = sorted({int(time[:2]) * 60 + int(time[3:5]) for time in times})
    return max((right - left for left, right in zip(minutes, minutes[1:])), default=0)


def _scalar(sql: str, params: tuple[Any, ...]) -> int:
    with transaction() as conn:
        row = conn.execute(sql, params).fetchone()
        return int(row[0] or 0)


if __name__ == "__main__":
    unittest.main()
