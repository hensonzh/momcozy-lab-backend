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
    def test_lactation_and_feeding_history_excludes_today(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"
            try:
                module = _load_seed_module()
                module.TODAY = datetime(2026, 5, 27)
                module.DELIVERY_DATE = "2026-03-07"

                with module._connect() as conn:
                    module._seed_profiles(conn)
                    module._seed_lactation(conn)
                    module._seed_feeding(conn)
                    conn.commit()

                conn = sqlite3.connect(data_store.DB_PATH)
                try:
                    today_pumping = conn.execute(
                        "SELECT COUNT(*) FROM pumping_log WHERE user_id = ? AND pump_start_time BETWEEN ? AND ?",
                        (module.USER_ID, "2026-05-27 00:00:00", "2026-05-27 23:59:59"),
                    ).fetchone()[0]
                    today_feeding = conn.execute(
                        "SELECT COUNT(*) FROM feeding_log WHERE user_id = ? AND feed_time BETWEEN ? AND ?",
                        (module.USER_ID, "2026-05-27 00:00:00", "2026-05-27 23:59:59"),
                    ).fetchone()[0]
                    yesterday_pumping = conn.execute(
                        "SELECT COUNT(*) FROM pumping_log WHERE user_id = ? AND pump_start_time BETWEEN ? AND ?",
                        (module.USER_ID, "2026-05-26 00:00:00", "2026-05-26 23:59:59"),
                    ).fetchone()[0]
                    yesterday_feeding = conn.execute(
                        "SELECT COUNT(*) FROM feeding_log WHERE user_id = ? AND feed_time BETWEEN ? AND ?",
                        (module.USER_ID, "2026-05-26 00:00:00", "2026-05-26 23:59:59"),
                    ).fetchone()[0]
                finally:
                    conn.close()
            finally:
                data_store.DB_PATH = old_db_path

        self.assertEqual(today_pumping, 0)
        self.assertEqual(today_feeding, 0)
        self.assertEqual(yesterday_pumping, 5)
        self.assertEqual(yesterday_feeding, 5)

    def test_lactation_and_feeding_seed_preserves_today_user_records(self) -> None:
        old_db_path = data_store.DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            data_store.DB_PATH = Path(tmp) / "milk_management.db"
            try:
                module = _load_seed_module()
                module.TODAY = datetime(2026, 5, 27)
                module.DELIVERY_DATE = "2026-03-07"

                with module._connect() as conn:
                    module._seed_profiles(conn)
                    conn.execute(
                        """
                        INSERT INTO pumping_log(
                            user_id, pump_start_time, pump_end_time, pump_milk_volum,
                            pump_type, pump_milk_duration, pump_source, pump_title, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            module.USER_ID,
                            "2026-05-27 07:30:00",
                            "2026-05-27 07:50:00",
                            80.0,
                            1,
                            20,
                            1,
                            "用户补录",
                            "2026-05-27 07:50:00",
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO feeding_log(
                            user_id, infant_id, feed_time, feed_milk_volum,
                            feed_type, feeding_title, feed_action, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            module.USER_ID,
                            module.INFANT_ID,
                            "2026-05-27 08:10:00",
                            60.0,
                            "瓶喂母乳",
                            "用户记录",
                            0,
                            "2026-05-27 08:10:00",
                        ),
                    )
                    module._seed_lactation(conn)
                    module._seed_feeding(conn)
                    conn.commit()

                conn = sqlite3.connect(data_store.DB_PATH)
                try:
                    today_pumping = conn.execute(
                        "SELECT COUNT(*) FROM pumping_log WHERE user_id = ? AND pump_start_time BETWEEN ? AND ?",
                        (module.USER_ID, "2026-05-27 00:00:00", "2026-05-27 23:59:59"),
                    ).fetchone()[0]
                    today_feeding = conn.execute(
                        "SELECT COUNT(*) FROM feeding_log WHERE user_id = ? AND feed_time BETWEEN ? AND ?",
                        (module.USER_ID, "2026-05-27 00:00:00", "2026-05-27 23:59:59"),
                    ).fetchone()[0]
                finally:
                    conn.close()
            finally:
                data_store.DB_PATH = old_db_path

        self.assertEqual(today_pumping, 1)
        self.assertEqual(today_feeding, 1)


def _load_seed_module():
    spec = importlib.util.spec_from_file_location("seed_status_demo_data_under_test", ROOT / "scripts" / "seed_status_demo_data.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load seed_status_demo_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
