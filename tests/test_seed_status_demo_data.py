from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from momcozy_agent.services import data_store


ROOT = Path(__file__).resolve().parents[1]


class SeedStatusDemoDataTest(unittest.TestCase):
    def test_startup_seed_aligns_today_and_history_plan_records(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"
            try:
                module = _load_seed_module()
                module.TODAY = datetime(2026, 5, 27)
                module.DELIVERY_DATE = "2026-03-07"
                module._current_demo_time = lambda: datetime(2026, 5, 27, 16, 30)
                data_store.init_db()
                with sqlite3.connect(data_store.DB_PATH) as conn:
                    conn.execute(
                        """
                        INSERT INTO pregnancy_diary_entry(user_id, entry_date, gestational_week, content, attachments_json)
                        VALUES (?, '2026-05-21', '孕32周', '旧 demo 日记', '[]')
                        """,
                        (module.LEGACY_PREGNANCY_DIARY_DEMO_USER_ID,),
                    )
                    conn.execute(
                        "INSERT INTO demo_seed_state(user_id, seed_key) VALUES (?, 'pregnancy_diary_demo_v1')",
                        (module.LEGACY_PREGNANCY_DIARY_DEMO_USER_ID,),
                    )
                    conn.execute(
                        """
                        INSERT INTO pregnancy_diary_entry(user_id, entry_date, gestational_week, content, attachments_json)
                        VALUES (?, '2026-05-21', '孕32周', '旧默认 demo 日记', '[]')
                        """,
                        (module.USER_ID,),
                    )
                    conn.execute(
                        "INSERT INTO demo_seed_state(user_id, seed_key) VALUES (?, 'pregnancy_diary_demo_v1')",
                        (module.USER_ID,),
                    )

                module.main()

                conn = sqlite3.connect(data_store.DB_PATH)
                try:
                    today_calendar = _scalar(
                        conn,
                        "SELECT COUNT(*) FROM calendar WHERE user_id = ? AND date = ? AND type = '吸奶'",
                        (module.USER_ID, "2026-05-27"),
                    )
                    today_done = _scalar(
                        conn,
                        "SELECT COUNT(*) FROM calendar WHERE user_id = ? AND date = ? AND type = '吸奶' AND finish = 'true'",
                        (module.USER_ID, "2026-05-27"),
                    )
                    today_records = _scalar(
                        conn,
                        "SELECT COUNT(*) FROM pumping_log WHERE user_id = ? AND date(pump_start_time) = ?",
                        (module.USER_ID, "2026-05-27"),
                    )
                    yesterday_calendar = _scalar(
                        conn,
                        "SELECT COUNT(*) FROM calendar WHERE user_id = ? AND date = ? AND type = '吸奶'",
                        (module.USER_ID, "2026-05-26"),
                    )
                    yesterday_done = _scalar(
                        conn,
                        "SELECT COUNT(*) FROM calendar WHERE user_id = ? AND date = ? AND type = '吸奶' AND finish = 'true'",
                        (module.USER_ID, "2026-05-26"),
                    )
                    yesterday_records = _scalar(
                        conn,
                        "SELECT COUNT(*) FROM pumping_log WHERE user_id = ? AND date(pump_start_time) = ?",
                        (module.USER_ID, "2026-05-26"),
                    )
                    today_feeding = _scalar(
                        conn,
                        "SELECT COUNT(*) FROM feeding_log WHERE user_id = ? AND date(feed_time) = ?",
                        (module.USER_ID, "2026-05-27"),
                    )
                    yesterday_nursing = _scalar(
                        conn,
                        "SELECT COUNT(*) FROM feeding_log WHERE user_id = ? AND date(feed_time) = ? AND feed_type = '亲喂'",
                        (module.USER_ID, "2026-05-26"),
                    )
                    estimate_anchor = _scalar(
                        conn,
                        "SELECT COUNT(*) FROM feeding_log WHERE user_id = ? AND feed_type = '瓶喂母乳' AND feeding_title = '亲喂估算参考'",
                        (module.USER_ID,),
                    )
                    recent_rows = conn.execute(
                        """
                        SELECT date(pump_start_time) AS day, SUM(pump_milk_volum) AS total
                        FROM pumping_log
                        WHERE user_id = ?
                          AND date(pump_start_time) BETWEEN ? AND ?
                        GROUP BY day
                        ORDER BY day
                        """,
                        (module.USER_ID, "2026-05-20", "2026-05-26"),
                    ).fetchall()
                    recent_nursing = {
                        str(row[0]): int(row[1] or 0)
                        for row in conn.execute(
                            """
                            SELECT date(feed_time) AS day, COUNT(*) AS count
                            FROM feeding_log
                            WHERE user_id = ?
                              AND feed_type = '亲喂'
                              AND date(feed_time) BETWEEN ? AND ?
                            GROUP BY day
                            """,
                            (module.USER_ID, "2026-05-20", "2026-05-26"),
                        ).fetchall()
                    }
                    earlier_month_rows = conn.execute(
                        """
                        SELECT date(pump_start_time) AS day, SUM(pump_milk_volum) AS total, COUNT(*) AS records
                        FROM pumping_log
                        WHERE user_id = ?
                          AND date(pump_start_time) BETWEEN ? AND ?
                        GROUP BY day
                        ORDER BY day
                        """,
                        (module.USER_ID, "2026-04-27", "2026-05-19"),
                    ).fetchall()
                    future_calendar = _scalar(
                        conn,
                        "SELECT COUNT(*) FROM calendar WHERE user_id = ? AND date > ?",
                        (module.USER_ID, "2026-05-27"),
                    )
                    pregnancy_diary_count = _scalar(conn, "SELECT COUNT(*) FROM pregnancy_diary_entry", ())
                finally:
                    conn.close()
            finally:
                data_store.DB_PATH = old_db_path

        self.assertEqual(today_calendar, 8)
        self.assertEqual(today_done, 5)
        self.assertEqual(today_records, 5)
        self.assertEqual(yesterday_calendar, 8)
        self.assertEqual(yesterday_done, 8)
        self.assertEqual(yesterday_records, 8)
        self.assertEqual(today_feeding, 0)
        self.assertTrue(2 <= yesterday_nursing <= 4)
        self.assertEqual(estimate_anchor, 1)
        self.assertEqual(len(earlier_month_rows), 23)
        self.assertEqual(len(recent_rows), 7)
        earlier_month_deltas = []
        earlier_missing_record_days = 0
        for day_text, pump_total, record_count in earlier_month_rows:
            reference = module.get_yield_reference_range((datetime.fromisoformat(day_text).date() - datetime.fromisoformat(module.DELIVERY_DATE).date()).days + 1)
            earlier_month_deltas.append(float(pump_total or 0) - float(reference["p15"]))
            if int(record_count or 0) < 8:
                earlier_missing_record_days += 1
        earlier_normal_days = sum(1 for delta in earlier_month_deltas if delta >= 0)
        earlier_low_days = sum(1 for delta in earlier_month_deltas if delta < 0)
        self.assertGreater(earlier_normal_days, earlier_low_days)
        self.assertGreaterEqual(earlier_missing_record_days, 3)
        self.assertGreaterEqual(len({round(delta) for delta in earlier_month_deltas}), 12)
        deltas = []
        estimate_deltas = []
        for day_text, pump_total in recent_rows:
            reference = module.get_yield_reference_range((datetime.fromisoformat(day_text).date() - datetime.fromisoformat(module.DELIVERY_DATE).date()).days + 1)
            deltas.append(float(pump_total or 0) - float(reference["p15"]))
            estimate_deltas.append(float(pump_total or 0) + recent_nursing.get(str(day_text), 0) * module.NURSING_ESTIMATE_ML - float(reference["p15"]))
        normal_days = sum(1 for delta in deltas if delta >= 0)
        low_days = sum(1 for delta in deltas if delta < 0)
        self.assertLess(normal_days, low_days)
        self.assertEqual(normal_days, 2)
        self.assertTrue(any(-110 <= delta < 0 for delta in deltas))
        self.assertTrue(any(delta <= -120 for delta in deltas))
        self.assertLess(deltas[-1] - deltas[0], -180)
        estimate_normal_days = sum(1 for delta in estimate_deltas if delta >= 0)
        estimate_low_days = sum(1 for delta in estimate_deltas if delta < 0)
        self.assertLess(estimate_normal_days, estimate_low_days)
        self.assertEqual(estimate_normal_days, 2)
        self.assertEqual(future_calendar, 0)
        self.assertEqual(pregnancy_diary_count, 0)

    def test_startup_seed_removes_records_not_in_plan_list(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"
            try:
                module = _load_seed_module()
                module.TODAY = datetime(2026, 5, 27)
                module.DELIVERY_DATE = "2026-03-07"
                module._current_demo_time = lambda: datetime(2026, 5, 27, 16, 30)

                with module._connect() as conn:
                    module._seed_profiles(conn)
                    conn.execute(
                        """
                        INSERT INTO calendar(user_id, date, task_id, start_time, content, type, source, is_milk_pump, finish)
                        VALUES (?, '2026-05-28', 1, '2026-05-28 09:00:00', '未来吸奶', '吸奶', '系统生成', 1, 'false')
                        """,
                        (module.USER_ID,),
                    )
                    conn.execute(
                        """
                        INSERT INTO pumping_log(user_id, pump_start_time, pump_end_time, pump_milk_volum, pump_type, pump_source, pump_title)
                        VALUES (?, '2026-05-26 03:33:00', '2026-05-26 03:53:00', 88, 1, 1, '不在计划里的记录')
                        """,
                        (module.USER_ID,),
                    )
                    conn.execute(
                        """
                        INSERT INTO feeding_log(user_id, infant_id, feed_time, feed_type, feed_milk_volum, feed_action, feeding_title)
                        VALUES (?, ?, '2026-05-26 08:00:00', '亲喂', 20, 0, '不在计划里的喂养')
                        """,
                        (module.USER_ID, module.INFANT_ID),
                    )
                    conn.commit()

                module.main()

                conn = sqlite3.connect(data_store.DB_PATH)
                try:
                    stray_pumping = _scalar(
                        conn,
                        "SELECT COUNT(*) FROM pumping_log WHERE user_id = ? AND time(pump_start_time) = '03:33:00'",
                        (module.USER_ID,),
                    )
                    stray_feeding = _scalar(
                        conn,
                        "SELECT COUNT(*) FROM feeding_log WHERE user_id = ? AND feeding_title = '不在计划里的喂养'",
                        (module.USER_ID,),
                    )
                    yesterday_nursing = _scalar(
                        conn,
                        "SELECT COUNT(*) FROM feeding_log WHERE user_id = ? AND date(feed_time) = ? AND feed_type = '亲喂'",
                        (module.USER_ID, "2026-05-26"),
                    )
                    future_calendar = _scalar(
                        conn,
                        "SELECT COUNT(*) FROM calendar WHERE user_id = ? AND date > ?",
                        (module.USER_ID, "2026-05-27"),
                    )
                    yesterday_records = _scalar(
                        conn,
                        "SELECT COUNT(*) FROM pumping_log WHERE user_id = ? AND date(pump_start_time) = ?",
                        (module.USER_ID, "2026-05-26"),
                    )
                finally:
                    conn.close()
            finally:
                data_store.DB_PATH = old_db_path

        self.assertEqual(stray_pumping, 0)
        self.assertEqual(stray_feeding, 0)
        self.assertTrue(2 <= yesterday_nursing <= 4)
        self.assertEqual(future_calendar, 0)
        self.assertEqual(yesterday_records, 8)


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> int:
    return int(conn.execute(sql, params).fetchone()[0] or 0)


def _load_seed_module():
    spec = importlib.util.spec_from_file_location("seed_status_demo_data_under_test", ROOT / "scripts" / "seed_status_demo_data.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load seed_status_demo_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
