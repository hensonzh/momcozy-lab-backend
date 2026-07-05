from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from momcozy_agent.services import data_store


class PlanRecordSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_db_path = data_store.DB_PATH
        self._tmp = tempfile.TemporaryDirectory(prefix="momcozy-plan-sync-")
        data_store.DB_PATH = Path(self._tmp.name) / "milk_management.db"  # type: ignore[assignment]
        data_store.init_db()

    def tearDown(self) -> None:
        data_store.DB_PATH = self._old_db_path  # type: ignore[assignment]
        self._tmp.cleanup()

    def test_revise_plan_task_done_creates_and_clears_plan_pumping_record(self) -> None:
        _seed_user("sync-pump")
        _seed_task("sync-pump", task_id=1, task_type="吸奶", is_milk_pump=1, start_time="09:00")

        completed = data_store.revise_plan_task(
            user_id="sync-pump",
            target_date="2026-05-26",
            task_id=1,
            task_time="09:00",
            task_content="吸奶",
            task_done="true",
        )

        self.assertTrue(completed)
        self.assertEqual(_scalar("SELECT COUNT(*) FROM pumping_log WHERE user_id = 'sync-pump' AND pump_source = 2"), 1)
        self.assertEqual(_scalar("SELECT pump_type FROM pumping_log WHERE user_id = 'sync-pump'"), 1)
        self.assertEqual(_scalar_text("SELECT finish FROM calendar WHERE user_id = 'sync-pump' AND task_id = 1"), "true")

        replay = data_store.revise_plan_task(
            user_id="sync-pump",
            target_date="2026-05-26",
            task_id=1,
            task_time="09:00",
            task_content="吸奶",
            task_done="true",
        )

        self.assertTrue(replay)
        self.assertEqual(_scalar("SELECT COUNT(*) FROM pumping_log WHERE user_id = 'sync-pump' AND pump_source = 2"), 1)

        cancelled = data_store.revise_plan_task(
            user_id="sync-pump",
            target_date="2026-05-26",
            task_id=1,
            task_time="09:00",
            task_content="吸奶",
            task_done="false",
        )

        self.assertTrue(cancelled)
        self.assertEqual(_scalar("SELECT COUNT(*) FROM pumping_log WHERE user_id = 'sync-pump' AND pump_source = 2"), 0)
        self.assertEqual(_scalar_text("SELECT finish FROM calendar WHERE user_id = 'sync-pump' AND task_id = 1"), "false")

    def test_revise_plan_task_done_reuses_frontend_uploaded_plan_record(self) -> None:
        _seed_user("sync-existing")
        _seed_task("sync-existing", task_id=1, task_type="吸奶", is_milk_pump=1, start_time="09:00")
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO pumping_log(
                    user_id, pump_start_time, pump_end_time, pump_milk_volum,
                    pump_type, pump_milk_duration, pump_source, pump_title
                )
                VALUES ('sync-existing', '2026-05-26 09:00:00', '2026-05-26 09:20:00', 88, 1, 20, 2, '吸奶')
                """
            )

        completed = data_store.revise_plan_task(
            user_id="sync-existing",
            target_date="2026-05-26",
            task_id=1,
            task_time="09:00",
            task_content="吸奶",
            task_done="true",
        )

        self.assertTrue(completed)
        self.assertEqual(_scalar("SELECT COUNT(*) FROM pumping_log WHERE user_id = 'sync-existing' AND pump_source = 2"), 1)
        self.assertEqual(_scalar("SELECT pump_type FROM pumping_log WHERE user_id = 'sync-existing'"), 1)
        self.assertEqual(_scalar("SELECT pump_milk_volum FROM pumping_log WHERE user_id = 'sync-existing'"), 88)

    def test_revise_plan_task_done_syncs_nursing_record(self) -> None:
        _seed_user("sync-nursing")
        _seed_task("sync-nursing", task_id=1, task_type="亲喂", is_milk_pump=0, start_time="08:00")
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO feeding_log(
                    user_id, infant_id, feed_time, feed_type, feed_milk_volum,
                    feed_action, feeding_title
                )
                VALUES ('sync-nursing', 1, '2026-05-26 08:00:00', '亲喂', 20, 1, '亲喂')
                """
            )

        completed = data_store.revise_plan_task(
            user_id="sync-nursing",
            target_date="2026-05-26",
            task_id=1,
            task_time="08:00",
            task_content="亲喂",
            task_done="true",
        )

        self.assertTrue(completed)
        self.assertEqual(_scalar("SELECT COUNT(*) FROM feeding_log WHERE user_id = 'sync-nursing' AND feed_action = 1"), 1)
        self.assertEqual(_scalar("SELECT COUNT(*) FROM pumping_log WHERE user_id = 'sync-nursing' AND pump_type = 2 AND pump_source = 2"), 0)

        skipped = data_store.revise_plan_task(
            user_id="sync-nursing",
            target_date="2026-05-26",
            task_id=1,
            task_time="08:00",
            task_content="亲喂",
            task_done="jump",
        )

        self.assertTrue(skipped)
        self.assertEqual(_scalar("SELECT COUNT(*) FROM feeding_log WHERE user_id = 'sync-nursing' AND feed_action = 1"), 0)
        self.assertEqual(_scalar("SELECT COUNT(*) FROM pumping_log WHERE user_id = 'sync-nursing' AND pump_type = 2 AND pump_source = 2"), 0)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(data_store.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_user(user_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO user_profile(user_id, user_nickname, delivery_date) VALUES (?, 'Test User', '2026-04-14')",
            (user_id,),
        )
        conn.execute(
            """
            INSERT INTO infant_profile(user_id, user_nickname, infant_name, sex, birth_date)
            VALUES (?, 'Test User', 'Baby', 'female', '2026-04-14')
            """,
            (user_id,),
        )


def _seed_task(user_id: str, *, task_id: int, task_type: str, is_milk_pump: int, start_time: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO calendar(user_id, date, task_id, start_time, end_time, content, type, source, is_milk_pump, finish)
            VALUES (?, '2026-05-26', ?, ?, ?, ?, ?, '系统生成', ?, 'false')
            """,
            (
                user_id,
                int(task_id),
                f"2026-05-26 {start_time}:00",
                f"2026-05-26 {start_time[:3]}20:00",
                task_type,
                task_type,
                int(is_milk_pump),
            ),
        )


def _scalar(sql: str) -> int:
    with _connect() as conn:
        row = conn.execute(sql).fetchone()
        return int(row[0] or 0)


def _scalar_text(sql: str) -> str:
    with _connect() as conn:
        row = conn.execute(sql).fetchone()
        return str(row[0] or "") if row else ""


if __name__ == "__main__":
    unittest.main()
