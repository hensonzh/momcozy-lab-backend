from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .milk_management.assessment import get_yield_reference_range
from .milk_management.feeding import estimate_breastfeeding_milk
from .paths import DATA_ROOT


DB_PATH = Path(os.getenv("MILK_DB_PATH", str(DATA_ROOT / "milk_management.db")))
_DB_INIT_LOCK = threading.Lock()
_INITIALIZED_DB_PATH: Path | None = None

FEED_TYPE_CODE_TO_TEXT = {
    0: "亲喂",
    1: "瓶喂母乳",
    2: "配方奶",
}
FEED_TYPE_TEXT_TO_CODE = {
    "亲喂": 0,
    "瓶喂母乳": 1,
    "配方奶": 2,
}
USER_PROFILE_COLUMNS = (
    "user_id",
    # Deprecated legacy display-name fallback. New profile writes and UI/API
    # contracts should use `display_name`; keep this column for old milk data.
    "user_nickname",
    "display_name",
    "age",
    "profile_onboarding_skipped_at",
    "profile_onboarding_completed_at",
    "birth_prep_due_date_or_week",
    "birth_prep_ivf",
    "birth_prep_fetus_count",
    "birth_prep_city_or_country",
    "birth_prep_birth_hospital",
    "birth_prep_birth_path",
    "birth_prep_first_birth",
    "birth_prep_feeding_intention",
    "birth_prep_return_to_work_timing",
    "birth_prep_support_person",
    "birth_prep_pregnancy_history_or_notes",
    "birth_prep_top_worries",
    "delivery_date",
    "lactation_advice",
    "feeding_advice",
    "daily_summary",
    "updated_at",
    "created_at",
)
USER_PROFILE_SELECT = ", ".join(USER_PROFILE_COLUMNS)
DEPRECATED_PROFILE_COLUMNS = {
    "user_profile.user_nickname": "Legacy display-name fallback; use user_profile.display_name for new code.",
    "infant_profile.user_nickname": "Legacy duplicated parent nickname; use user_profile.display_name for new code.",
}


def init_db(*, force: bool = False) -> None:
    global _INITIALIZED_DB_PATH

    db_path = Path(DB_PATH)
    if not force and _INITIALIZED_DB_PATH == db_path and db_path.exists():
        return

    with _DB_INIT_LOCK:
        db_path = Path(DB_PATH)
        if not force and _INITIALIZED_DB_PATH == db_path and db_path.exists():
            return

        db_path.parent.mkdir(parents=True, exist_ok=True)
        with _connect() as conn:
            conn.executescript(
                """
            CREATE TABLE IF NOT EXISTS user_profile (
                user_id TEXT PRIMARY KEY,
                user_nickname TEXT,
                display_name TEXT,
                age INTEGER,
                profile_onboarding_skipped_at TEXT,
                profile_onboarding_completed_at TEXT,
                birth_prep_due_date_or_week TEXT,
                birth_prep_ivf TEXT,
                birth_prep_fetus_count TEXT,
                birth_prep_city_or_country TEXT,
                birth_prep_birth_hospital TEXT,
                birth_prep_birth_path TEXT,
                birth_prep_first_birth TEXT,
                birth_prep_feeding_intention TEXT,
                birth_prep_return_to_work_timing TEXT,
                birth_prep_support_person TEXT,
                birth_prep_pregnancy_history_or_notes TEXT,
                birth_prep_top_worries TEXT,
                delivery_date TEXT,
                lactation_advice TEXT,
                feeding_advice TEXT,
                daily_summary TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS infant_profile (
                infant_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                user_nickname TEXT,
                infant_name TEXT NOT NULL,
                sex TEXT NOT NULL,
                birth_date TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_infant_profile_user ON infant_profile(user_id);

            CREATE TABLE IF NOT EXISTS feeding_log (
                feeding_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                infant_id INTEGER NOT NULL,
                feed_time TEXT NOT NULL,
                feed_milk_volum REAL,
                feed_type TEXT,
                feeding_title TEXT,
                feed_action INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_feeding_user_infant_time ON feeding_log(user_id, infant_id, feed_time);

            CREATE TABLE IF NOT EXISTS infant_growth_log (
                growth_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                infant_id INTEGER NOT NULL,
                height_cm REAL,
                weight_kg REAL,
                head_cm REAL,
                height_measured_at TEXT NOT NULL,
                weight_measured_at TEXT NOT NULL,
                head_measured_at TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_growth_user_infant ON infant_growth_log(user_id, infant_id, growth_id DESC);

            CREATE TABLE IF NOT EXISTS pumping_log (
                pumping_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                pump_start_time TEXT NOT NULL,
                pump_end_time TEXT NOT NULL,
                pump_milk_volum REAL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                pump_type INTEGER DEFAULT 0,
                pump_milk_duration INTEGER,
                pump_source INTEGER NOT NULL DEFAULT 1,
                pump_title TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_pumping_user_time ON pumping_log(user_id, pump_start_time);

            CREATE TABLE IF NOT EXISTS milk_plan (
                plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                plan_name TEXT NOT NULL,
                plan_type TEXT,
                plan_days INTEGER NOT NULL,
                plan_summary TEXT,
                plan_payload_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                milestone_summary TEXT,
                milestone_list TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_plan_user_type ON milk_plan(user_id, plan_type, plan_id DESC);

            CREATE TABLE IF NOT EXISTS care_plan_artifact (
                plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                plan_type TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                payload_json TEXT NOT NULL,
                source_artifact_type TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_care_plan_artifact_user ON care_plan_artifact(user_id, status, updated_at DESC);

            CREATE TABLE IF NOT EXISTS pregnancy_diary_entry (
                entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                entry_date TEXT NOT NULL,
                gestational_week TEXT,
                mood TEXT,
                energy_level TEXT,
                sleep_summary TEXT,
                fetal_movement TEXT,
                symptom_tags_json TEXT,
                appointment_note TEXT,
                nutrition_note TEXT,
                content TEXT,
                attachments_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_pregnancy_diary_user_date ON pregnancy_diary_entry(user_id, entry_date DESC, entry_id DESC);

            CREATE TABLE IF NOT EXISTS pregnancy_diary_health_note (
                note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                entry_date TEXT NOT NULL,
                topic TEXT,
                user_report TEXT,
                asked_questions_json TEXT,
                known_answers_json TEXT,
                suggestion_summary TEXT,
                follow_up TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_pregnancy_diary_health_note_entry ON pregnancy_diary_health_note(entry_id, note_id DESC);
            CREATE INDEX IF NOT EXISTS idx_pregnancy_diary_health_note_user_date ON pregnancy_diary_health_note(user_id, entry_date DESC, note_id DESC);

            CREATE TABLE IF NOT EXISTS demo_seed_state (
                user_id TEXT NOT NULL,
                seed_key TEXT NOT NULL,
                seeded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, seed_key)
            );

            CREATE TABLE IF NOT EXISTS calendar (
                item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                plan_id INTEGER,
                date TEXT,
                task_id INTEGER,
                start_time TEXT,
                end_time TEXT,
                content TEXT,
                type TEXT NOT NULL CHECK(type IN ('吸奶', '亲喂', '自定义')),
                source TEXT NOT NULL DEFAULT '系统生成' CHECK(source IN ('系统生成', '用户输入')),
                is_milk_pump INTEGER NOT NULL DEFAULT 0 CHECK(is_milk_pump IN (0, 1)),
                finish TEXT NOT NULL DEFAULT 'false' CHECK(finish IN ('true', 'false', 'jump')),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                modified_at DATETIME
            );
            CREATE INDEX IF NOT EXISTS idx_calendar_user_date ON calendar(user_id, date, start_time);

            CREATE TABLE IF NOT EXISTS pump_threshold (
                user_id TEXT PRIMARY KEY,
                stimulate_level_l INTEGER NOT NULL,
                deep_level_l INTEGER NOT NULL,
                stimulate_level_r INTEGER NOT NULL,
                deep_level_r INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pump_health (
                user_id TEXT PRIMARY KEY,
                health_l INTEGER NOT NULL,
                health_r INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pump_device_info (
                user_id TEXT PRIMARY KEY,
                device_left_json TEXT NOT NULL,
                device_right_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pump_workstate_event (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_workstate_user_id ON pump_workstate_event(user_id, id DESC);

            CREATE TABLE IF NOT EXISTS pump_workstate_pending_reply (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                output TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                pulled_at TEXT
            );

            CREATE TABLE IF NOT EXISTS pump_process_point (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pump_workstate_reply_state (
                user_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pump_process_reply_state (
                user_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS uploaded_file (
                file_id TEXT PRIMARY KEY,
                original_name TEXT NOT NULL,
                extension TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                size INTEGER NOT NULL,
                path TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
                """
            )
            _ensure_column(conn, "user_profile", "lactation_advice", "TEXT")
            _ensure_column(conn, "user_profile", "feeding_advice", "TEXT")
            _ensure_column(conn, "user_profile", "daily_summary", "TEXT")
            _ensure_column(conn, "user_profile", "display_name", "TEXT")
            _ensure_column(conn, "user_profile", "age", "INTEGER")
            _ensure_column(conn, "user_profile", "profile_onboarding_skipped_at", "TEXT")
            _ensure_column(conn, "user_profile", "profile_onboarding_completed_at", "TEXT")
            _ensure_column(conn, "user_profile", "birth_prep_due_date_or_week", "TEXT")
            _ensure_column(conn, "user_profile", "birth_prep_ivf", "TEXT")
            _ensure_column(conn, "user_profile", "birth_prep_fetus_count", "TEXT")
            _ensure_column(conn, "user_profile", "birth_prep_city_or_country", "TEXT")
            _ensure_column(conn, "user_profile", "birth_prep_birth_hospital", "TEXT")
            _ensure_column(conn, "user_profile", "birth_prep_birth_path", "TEXT")
            _ensure_column(conn, "user_profile", "birth_prep_first_birth", "TEXT")
            _ensure_column(conn, "user_profile", "birth_prep_feeding_intention", "TEXT")
            _ensure_column(conn, "user_profile", "birth_prep_return_to_work_timing", "TEXT")
            _ensure_column(conn, "user_profile", "birth_prep_support_person", "TEXT")
            _ensure_column(conn, "user_profile", "birth_prep_pregnancy_history_or_notes", "TEXT")
            _ensure_column(conn, "user_profile", "birth_prep_top_worries", "TEXT")
            _ensure_calendar_schema(conn)
            _ensure_column(conn, "feeding_log", "feed_action", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(conn, "feeding_log", "feeding_title", "TEXT")
            _ensure_column(conn, "pumping_log", "pump_source", "INTEGER NOT NULL DEFAULT 1")
            _ensure_column(conn, "pumping_log", "pump_title", "TEXT")
        _INITIALIZED_DB_PATH = db_path


def db_file() -> Path:
    init_db()
    return DB_PATH


def _ensure_calendar_schema(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'calendar'").fetchone()
    create_sql = str(row["sql"] if row else "")
    if "finish TEXT" in create_sql and "finish IN ('true', 'false', 'jump')" in create_sql:
        return

    conn.execute("DROP INDEX IF EXISTS idx_calendar_user_date")
    conn.execute("ALTER TABLE calendar RENAME TO calendar_legacy")
    conn.executescript(
        """
        CREATE TABLE calendar (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            plan_id INTEGER,
            date TEXT,
            task_id INTEGER,
            start_time TEXT,
            end_time TEXT,
            content TEXT,
            type TEXT NOT NULL CHECK(type IN ('吸奶', '亲喂', '自定义')),
            source TEXT NOT NULL DEFAULT '系统生成' CHECK(source IN ('系统生成', '用户输入')),
            is_milk_pump INTEGER NOT NULL DEFAULT 0 CHECK(is_milk_pump IN (0, 1)),
            finish TEXT NOT NULL DEFAULT 'false' CHECK(finish IN ('true', 'false', 'jump')),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            modified_at DATETIME
        );

        INSERT INTO calendar(
            item_id, user_id, plan_id, date, task_id, start_time, end_time,
            content, type, source, is_milk_pump, finish, created_at, modified_at
        )
        SELECT
            item_id,
            user_id,
            plan_id,
            date,
            task_id,
            start_time,
            end_time,
            content,
            CASE WHEN type IN ('吸奶', '亲喂', '自定义') THEN type ELSE '自定义' END,
            CASE WHEN source = '系统生成' THEN '系统生成' ELSE '用户输入' END,
            CASE WHEN CAST(is_milk_pump AS TEXT) IN ('1', 'true', 'True') THEN 1 ELSE 0 END,
            CASE
                WHEN lower(CAST(finish AS TEXT)) IN ('1', 'true', 'yes', 'done', 'completed') THEN 'true'
                WHEN lower(CAST(finish AS TEXT)) = 'jump' THEN 'jump'
                ELSE 'false'
            END,
            created_at,
            modified_at
        FROM calendar_legacy;

        DROP TABLE calendar_legacy;
        CREATE INDEX IF NOT EXISTS idx_calendar_user_date ON calendar(user_id, date, start_time);
        """
    )


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_spec: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    if any(str(row["name"]) == column_name for row in rows):
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_spec}")


def list_baby_profiles(user_id: str, infant_id: int | None = None) -> list[dict[str, Any]]:
    init_db()
    sql = "SELECT * FROM infant_profile WHERE user_id = ?"
    params: list[Any] = [user_id]
    if infant_id is not None:
        sql += " AND infant_id = ?"
        params.append(infant_id)
    sql += " ORDER BY infant_id"
    with _connect() as conn:
        return [_row_dict(row) for row in conn.execute(sql, params)]


def get_baby_profile(infant_id: int) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM infant_profile WHERE infant_id = ?", (infant_id,)).fetchone()
        return _row_dict(row) if row else None


def get_mom_baby_info(user_id: str) -> dict[str, Any] | None:
    init_db()
    uid = str(user_id or "").strip()
    if not uid:
        return None
    with _connect() as conn:
        user = conn.execute(f"SELECT {USER_PROFILE_SELECT} FROM user_profile WHERE user_id = ?", (uid,)).fetchone()
        baby = conn.execute(
            "SELECT * FROM infant_profile WHERE user_id = ? ORDER BY infant_id LIMIT 1",
            (uid,),
        ).fetchone()
    if not user and not baby:
        return None
    user_data = _row_dict(user) if user else {}
    baby_data = _row_dict(baby) if baby else {}
    delivery_date = str(user_data.get("delivery_date") or baby_data.get("birth_date") or "")
    return {
        "delivery_date": delivery_date,
        "infant_birth_date": str(baby_data.get("birth_date") or ""),
        "lactation_advice": user_data.get("lactation_advice"),
        "feeding_advice": user_data.get("feeding_advice"),
    }


def get_user_profile(user_id: str) -> dict[str, Any]:
    init_db()
    uid = str(user_id or "").strip()
    if not uid:
        return {}
    with _connect() as conn:
        row = conn.execute(f"SELECT {USER_PROFILE_SELECT} FROM user_profile WHERE user_id = ?", (uid,)).fetchone()
    if row is None:
        return _empty_user_profile(uid)
    profile = _row_dict(row)
    profile["user_id"] = uid
    profile["display_name"] = str(profile.get("display_name") or profile.get("user_nickname") or "").strip()
    profile["age"] = _profile_age_value(profile.get("age"))
    profile["birth_prep_due_date_or_week"] = _normalize_birth_prep_memory_text(profile.get("birth_prep_due_date_or_week"))
    profile["birth_prep_ivf"] = _normalize_birth_prep_memory_text(profile.get("birth_prep_ivf"))
    profile["birth_prep_fetus_count"] = _normalize_birth_prep_memory_text(profile.get("birth_prep_fetus_count"))
    profile["birth_prep_city_or_country"] = _normalize_birth_prep_memory_text(profile.get("birth_prep_city_or_country"))
    profile["birth_prep_birth_hospital"] = _normalize_birth_prep_memory_text(profile.get("birth_prep_birth_hospital"))
    profile["birth_prep_birth_path"] = _normalize_birth_prep_memory_text(profile.get("birth_prep_birth_path"))
    profile["birth_prep_first_birth"] = _normalize_birth_prep_memory_text(profile.get("birth_prep_first_birth"))
    profile["birth_prep_feeding_intention"] = _normalize_birth_prep_memory_text(profile.get("birth_prep_feeding_intention"))
    profile["birth_prep_return_to_work_timing"] = _normalize_birth_prep_memory_text(profile.get("birth_prep_return_to_work_timing"))
    profile["birth_prep_support_person"] = _normalize_birth_prep_memory_text(profile.get("birth_prep_support_person"))
    profile["birth_prep_pregnancy_history_or_notes"] = _normalize_birth_prep_memory_text(
        profile.get("birth_prep_pregnancy_history_or_notes")
    )
    profile["birth_prep_top_worries"] = _normalize_birth_prep_memory_text(profile.get("birth_prep_top_worries"))
    profile["profile_onboarding_complete"] = bool(profile.get("display_name")) and profile.get("age") is not None
    profile["profile_onboarding_skipped"] = bool(str(profile.get("profile_onboarding_skipped_at") or "").strip())
    return profile


def _empty_user_profile(user_id: str) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "display_name": "",
        "age": None,
        "birth_prep_due_date_or_week": "",
        "birth_prep_ivf": "",
        "birth_prep_fetus_count": "",
        "birth_prep_city_or_country": "",
        "birth_prep_birth_hospital": "",
        "birth_prep_birth_path": "",
        "birth_prep_first_birth": "",
        "birth_prep_feeding_intention": "",
        "birth_prep_return_to_work_timing": "",
        "birth_prep_support_person": "",
        "birth_prep_pregnancy_history_or_notes": "",
        "birth_prep_top_worries": "",
        "profile_onboarding_complete": False,
        "profile_onboarding_skipped": False,
    }


def reset_profile_onboarding_memory_for_dev() -> int:
    """Clear profile onboarding answers so local demo startup can ask again."""
    init_db()
    now = _now()
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE user_profile
            SET display_name = NULL,
                user_nickname = NULL,
                age = NULL,
                profile_onboarding_skipped_at = NULL,
                profile_onboarding_completed_at = NULL,
                updated_at = ?
            WHERE display_name IS NOT NULL
               OR user_nickname IS NOT NULL
               OR age IS NOT NULL
               OR profile_onboarding_skipped_at IS NOT NULL
               OR profile_onboarding_completed_at IS NOT NULL
            """,
            (now,),
        )
        return int(cursor.rowcount or 0)


def reset_birth_prep_profile_memory_for_dev() -> int:
    """Clear shared birth-prep answers so local demo startup can collect them again."""
    init_db()
    now = _now()
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE user_profile
            SET birth_prep_due_date_or_week = NULL,
                birth_prep_ivf = NULL,
                birth_prep_fetus_count = NULL,
                birth_prep_city_or_country = NULL,
                birth_prep_birth_hospital = NULL,
                birth_prep_birth_path = NULL,
                birth_prep_first_birth = NULL,
                birth_prep_feeding_intention = NULL,
                birth_prep_return_to_work_timing = NULL,
                birth_prep_support_person = NULL,
                birth_prep_pregnancy_history_or_notes = NULL,
                birth_prep_top_worries = NULL,
                updated_at = ?
            WHERE birth_prep_due_date_or_week IS NOT NULL
               OR birth_prep_ivf IS NOT NULL
               OR birth_prep_fetus_count IS NOT NULL
               OR birth_prep_city_or_country IS NOT NULL
               OR birth_prep_birth_hospital IS NOT NULL
               OR birth_prep_birth_path IS NOT NULL
               OR birth_prep_first_birth IS NOT NULL
               OR birth_prep_feeding_intention IS NOT NULL
               OR birth_prep_return_to_work_timing IS NOT NULL
               OR birth_prep_support_person IS NOT NULL
               OR birth_prep_pregnancy_history_or_notes IS NOT NULL
               OR birth_prep_top_worries IS NOT NULL
            """,
            (now,),
        )
        return int(cursor.rowcount or 0)


def reset_generated_plan_artifacts_for_dev() -> int:
    """Clear generated service plan artifacts from local demo startup."""
    init_db()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM care_plan_artifact")
        return int(cursor.rowcount or 0)


def reset_milk_plans_for_dev() -> int:
    """Clear generated milk-management plans from local demo startup."""
    init_db()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM milk_plan")
        return int(cursor.rowcount or 0)


def reset_uploaded_files_for_dev() -> int:
    """Clear uploaded-file metadata from local demo startup."""
    init_db()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM uploaded_file")
        return int(cursor.rowcount or 0)


def reset_device_runtime_state_for_dev() -> int:
    """Clear transient pump/device runtime rows from local demo startup."""
    init_db()
    tables = (
        "pump_threshold",
        "pump_health",
        "pump_device_info",
        "pump_workstate_event",
        "pump_workstate_pending_reply",
        "pump_process_point",
        "pump_workstate_reply_state",
        "pump_process_reply_state",
    )
    cleared = 0
    with _connect() as conn:
        for table in tables:
            cursor = conn.execute(f"DELETE FROM {table}")
            cleared += int(cursor.rowcount or 0)
    return cleared


def reset_non_status_demo_data_for_dev() -> dict[str, int]:
    """Keep seeded status demo rows, and clear generated/session data on local startup."""
    return {
        "profile_onboarding": reset_profile_onboarding_memory_for_dev(),
        "birth_prep_profile": reset_birth_prep_profile_memory_for_dev(),
        "generated_plans": reset_generated_plan_artifacts_for_dev(),
        "milk_plans": reset_milk_plans_for_dev(),
        "pregnancy_diary": reset_pregnancy_diary_for_dev(),
        "uploaded_files": reset_uploaded_files_for_dev(),
        "device_runtime": reset_device_runtime_state_for_dev(),
    }


def update_user_profile_memory(
    *,
    user_id: str,
    display_name: str | None = None,
    age: int | None = None,
    onboarding_skipped: bool | None = None,
) -> dict[str, Any] | None:
    init_db()
    uid = str(user_id or "").strip()
    if not uid:
        return None

    now = _now()
    name_value = _normalize_display_name(display_name) if display_name is not None else None
    age_value = _profile_age_value(age) if age is not None else None

    with _connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO user_profile(user_id, created_at, updated_at)
            VALUES (?, ?, ?)
            """,
            (uid, now, now),
        )
        assignments: list[str] = []
        values: list[Any] = []
        if name_value:
            assignments.append("display_name = ?")
            values.append(name_value)
            assignments.append("user_nickname = COALESCE(NULLIF(user_nickname, ''), ?)")
            values.append(name_value)
        if age_value is not None:
            assignments.append("age = ?")
            values.append(age_value)
        if onboarding_skipped is True:
            assignments.append("profile_onboarding_skipped_at = ?")
            values.append(now)
        elif onboarding_skipped is False:
            assignments.append("profile_onboarding_skipped_at = NULL")
        if name_value or age_value is not None:
            assignments.append("profile_onboarding_skipped_at = NULL")
        if assignments:
            assignments.append("updated_at = ?")
            values.append(now)
            values.append(uid)
            conn.execute(
                f"""
                UPDATE user_profile
                SET {", ".join(assignments)}
                WHERE user_id = ?
                """,
                tuple(values),
            )
        row = conn.execute(f"SELECT {USER_PROFILE_SELECT} FROM user_profile WHERE user_id = ?", (uid,)).fetchone()

    if row is None:
        return {"user_id": uid}
    profile = _row_dict(row)
    profile["display_name"] = str(profile.get("display_name") or profile.get("user_nickname") or "").strip()
    profile["age"] = _profile_age_value(profile.get("age"))
    completed = bool(profile.get("display_name")) and profile.get("age") is not None
    profile["profile_onboarding_complete"] = completed
    profile["profile_onboarding_skipped"] = bool(str(profile.get("profile_onboarding_skipped_at") or "").strip())
    if completed and not profile.get("profile_onboarding_completed_at"):
        with _connect() as conn:
            conn.execute(
                """
                UPDATE user_profile
                SET profile_onboarding_completed_at = ?,
                    updated_at = ?
                WHERE user_id = ?
                """,
                (now, now, uid),
            )
        profile["profile_onboarding_completed_at"] = now
    return profile


def update_birth_prep_profile_memory(
    *,
    user_id: str,
    age: Any = None,
    due_date_or_week: Any = None,
    ivf: Any = None,
    fetus_count: Any = None,
    city_or_country: Any = None,
    birth_hospital: Any = None,
    birth_path: Any = None,
    first_birth: Any = None,
    feeding_intention: Any = None,
    return_to_work_timing: Any = None,
    support_person: Any = None,
    pregnancy_history_or_notes: Any = None,
    top_worries: Any = None,
) -> dict[str, Any] | None:
    init_db()
    uid = str(user_id or "").strip()
    if not uid:
        return None

    values_by_column: dict[str, Any] = {
        "birth_prep_due_date_or_week": _normalize_birth_prep_memory_text(due_date_or_week),
        "birth_prep_ivf": _normalize_birth_prep_memory_text(ivf),
        "birth_prep_fetus_count": _normalize_birth_prep_memory_text(fetus_count),
        "birth_prep_city_or_country": _normalize_birth_prep_memory_text(city_or_country),
        "birth_prep_birth_hospital": _normalize_birth_prep_memory_text(birth_hospital),
        "birth_prep_birth_path": _normalize_birth_prep_memory_text(birth_path),
        "birth_prep_first_birth": _normalize_birth_prep_memory_text(first_birth),
        "birth_prep_feeding_intention": _normalize_birth_prep_memory_text(feeding_intention),
        "birth_prep_return_to_work_timing": _normalize_birth_prep_memory_text(return_to_work_timing),
        "birth_prep_support_person": _normalize_birth_prep_memory_text(support_person),
        "birth_prep_pregnancy_history_or_notes": _normalize_birth_prep_memory_text(pregnancy_history_or_notes),
        "birth_prep_top_worries": _normalize_birth_prep_memory_text(top_worries),
    }
    age_value = _profile_age_value(age) if age is not None else None
    if age_value is not None:
        values_by_column["age"] = age_value
    assignments: list[str] = []
    values: list[Any] = []
    for column, value in values_by_column.items():
        if not value:
            continue
        assignments.append(f"{column} = ?")
        values.append(value)

    now = _now()
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO user_profile(user_id, created_at, updated_at)
            VALUES (?, ?, ?)
            """,
            (uid, now, now),
        )
        if assignments:
            assignments.append("updated_at = ?")
            values.append(now)
            values.append(uid)
            conn.execute(
                f"""
                UPDATE user_profile
                SET {", ".join(assignments)}
                WHERE user_id = ?
                """,
                tuple(values),
            )
    return get_user_profile(uid)


def update_user_profile_advice(*, user_id: str, lactation_advice: str, feeding_advice: str) -> bool:
    init_db()
    uid = str(user_id or "").strip()
    if not uid:
        return False
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE user_profile
            SET lactation_advice = ?,
                feeding_advice = ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (str(lactation_advice or ""), str(feeding_advice or ""), _now(), uid),
        )
        return cursor.rowcount > 0


def update_user_profile_daily_summary(*, user_id: str, daily_summary: str) -> bool:
    init_db()
    uid = str(user_id or "").strip()
    if not uid:
        return False
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE user_profile
            SET daily_summary = ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (str(daily_summary or ""), _now(), uid),
        )
        return cursor.rowcount > 0


def get_status_advice_context(*, user_id: str, days: int = 7) -> dict[str, Any] | None:
    init_db()
    uid = str(user_id or "").strip()
    if not uid:
        return None
    lookback_days = max(1, int(days or 7))
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=lookback_days - 1)
    start_at = f"{start_dt.date().isoformat()} 00:00:00"
    end_at = f"{end_dt.date().isoformat()} 23:59:59"

    with _connect() as conn:
        user = conn.execute(f"SELECT {USER_PROFILE_SELECT} FROM user_profile WHERE user_id = ?", (uid,)).fetchone()
        baby = conn.execute(
            "SELECT * FROM infant_profile WHERE user_id = ? ORDER BY infant_id LIMIT 1",
            (uid,),
        ).fetchone()
        if not user and not baby:
            return None
        pumping_rows = conn.execute(
            """
            SELECT pumping_id, user_id, pump_start_time, pump_end_time, pump_milk_volum,
                   pump_type, pump_milk_duration, pump_source, pump_title, created_at
            FROM pumping_log
            WHERE user_id = ? AND pump_start_time >= ? AND pump_start_time <= ?
            ORDER BY pump_start_time ASC, pumping_id ASC
            """,
            (uid, start_at, end_at),
        ).fetchall()
        feeding_rows = conn.execute(
            """
            SELECT feeding_id, user_id, infant_id, feed_time, feed_milk_volum,
                   feed_type, feed_action, feeding_title, created_at
            FROM feeding_log
            WHERE user_id = ? AND feed_time >= ? AND feed_time <= ?
            ORDER BY feed_time ASC, feeding_id ASC
            """,
            (uid, start_at, end_at),
        ).fetchall()

    return {
        "user_profile": _row_dict(user) if user else {},
        "infant_profile": _row_dict(baby) if baby else {},
        "window": {
            "days": lookback_days,
            "start_at": start_at,
            "end_at": end_at,
        },
        "pumping_records": [_row_dict(row) for row in pumping_rows],
        "feeding_records": [_row_dict(row) for row in feeding_rows],
    }


def get_mom_baby_today_summary(user_id: str, target_date: str | None = None) -> dict[str, float] | None:
    init_db()
    uid = str(user_id or "").strip()
    if not uid:
        return None
    date_text = str(target_date or "").strip() or datetime.now().date().isoformat()
    start_at = f"{date_text} 00:00:00"
    end_at = f"{date_text} 23:59:59"
    with _connect() as conn:
        exists = conn.execute(
            """
            SELECT 1 FROM user_profile WHERE user_id = ?
            UNION
            SELECT 1 FROM infant_profile WHERE user_id = ?
            LIMIT 1
            """,
            (uid, uid),
        ).fetchone()
        if not exists:
            return None
        pumping_rows = conn.execute(
            """
            SELECT pump_milk_volum, pump_type, pump_milk_duration
            FROM pumping_log
            WHERE user_id = ? AND pump_start_time >= ? AND pump_start_time <= ?
            """,
            (uid, start_at, end_at),
        ).fetchall()
        feeding_rows = conn.execute(
            """
            SELECT feed_milk_volum, feed_type
            FROM feeding_log
            WHERE user_id = ? AND feed_time >= ? AND feed_time <= ?
            """,
            (uid, start_at, end_at),
        ).fetchall()

    breastfeeding_estimate = estimate_breastfeeding_milk(user_id=uid, as_of_time=end_at)

    pump_total = 0.0
    for row in pumping_rows:
        if int(row["pump_type"] or 0) == 2:
            pump_total += breastfeeding_estimate or 0.0
        else:
            pump_total += _to_float(row["pump_milk_volum"])

    feeding_total = 0.0
    feeding_estimated_total = 0.0
    for row in feeding_rows:
        feed_type = str(row["feed_type"] or "")
        amount = _to_float(row["feed_milk_volum"])
        if feed_type == FEED_TYPE_CODE_TO_TEXT[0]:
            feeding_total += breastfeeding_estimate or 0.0
            feeding_estimated_total += breastfeeding_estimate or 0.0
        else:
            feeding_total += amount

    return {
        "pump_milk_volum": round(pump_total, 1),
        "feeding_volum": round(feeding_total, 1),
        "feeding_forecast_volum": round(feeding_estimated_total, 1),
    }


def add_feeding_record(
    *,
    user_id: str,
    infant_id: int,
    feed_time: str,
    feed_type_code: int,
    feed_milk_volum: int,
    feed_action: int = 0,
    feeding_title: str = "",
) -> int:
    init_db()
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO feeding_log(user_id, infant_id, feed_time, feed_type, feed_milk_volum, feed_action, feeding_title, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                infant_id,
                feed_time,
                FEED_TYPE_CODE_TO_TEXT[feed_type_code],
                float(feed_milk_volum),
                int(feed_action),
                str(feeding_title or ""),
                _now(),
            ),
        )
        return int(cursor.lastrowid)


def delete_feeding_record(*, user_id: str, feeding_id: int) -> bool:
    init_db()
    uid = str(user_id or "").strip()
    if not uid:
        return False
    with _connect() as conn:
        feeding = conn.execute(
            """
            SELECT feed_time, feed_type, feed_milk_volum
            FROM feeding_log
            WHERE user_id = ? AND feeding_id = ?
            """,
            (uid, int(feeding_id)),
        ).fetchone()
        if not feeding:
            return False
        cursor = conn.execute("DELETE FROM feeding_log WHERE user_id = ? AND feeding_id = ?", (uid, int(feeding_id)))
        deleted = cursor.rowcount > 0
        if deleted and str(feeding["feed_type"] or "") == FEED_TYPE_CODE_TO_TEXT[0]:
            pump_id = _synced_breastfeeding_pumping_id(
                conn,
                user_id=uid,
                feed_time=str(feeding["feed_time"] or ""),
                feed_milk_volum=feeding["feed_milk_volum"],
            )
            if pump_id is not None:
                conn.execute(
                    "DELETE FROM pumping_log WHERE user_id = ? AND pumping_id = ?",
                    (uid, pump_id),
                )
        return deleted


def _synced_breastfeeding_pumping_id(conn: sqlite3.Connection, *, user_id: str, feed_time: str, feed_milk_volum: Any) -> int | None:
    try:
        duration = int(float(feed_milk_volum))
    except Exception:
        duration = None
    clauses = [
        "user_id = ?",
        "pump_start_time = ?",
        "pump_end_time = ?",
        "pump_type = 2",
        "pump_milk_volum IS NULL",
    ]
    params: list[Any] = [user_id, feed_time, feed_time]
    if duration is not None:
        clauses.append("pump_milk_duration = ?")
        params.append(duration)
    row = conn.execute(
        f"""
        SELECT pumping_id
        FROM pumping_log
        WHERE {' AND '.join(clauses)}
        ORDER BY pumping_id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    return int(row["pumping_id"]) if row else None


def list_feeding_records(*, user_id: str | None = None, infant_id: int | None = None, start_at: str | None = None, end_at: str | None = None) -> list[dict[str, Any]]:
    init_db()
    clauses = []
    params: list[Any] = []
    if user_id:
        clauses.append("user_id = ?")
        params.append(user_id)
    if infant_id is not None:
        clauses.append("infant_id = ?")
        params.append(infant_id)
    if start_at:
        clauses.append("feed_time >= ?")
        params.append(start_at)
    if end_at:
        clauses.append("feed_time <= ?")
        params.append(end_at)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect() as conn:
        return [_row_dict(row) for row in conn.execute(f"SELECT * FROM feeding_log{where} ORDER BY feed_time DESC", params)]


def resolve_infant_id_for_user(user_id: str, infant_id: int | None = None) -> int:
    init_db()
    uid = str(user_id or "").strip()
    if not uid:
        return 0
    if infant_id is not None:
        with _connect() as conn:
            row = conn.execute(
                "SELECT infant_id FROM infant_profile WHERE user_id = ? AND infant_id = ?",
                (uid, int(infant_id)),
            ).fetchone()
        return int(row["infant_id"]) if row else 0
    with _connect() as conn:
        row = conn.execute(
            "SELECT infant_id FROM infant_profile WHERE user_id = ? ORDER BY infant_id LIMIT 1",
            (uid,),
        ).fetchone()
    return int(row["infant_id"]) if row else 0


def add_growth_record(*, user_id: str, infant_id: int | None, height_cm: float, weight_kg: float, head_cm: float) -> int:
    init_db()
    now = _now()
    stored_infant_id = int(infant_id or 0)
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO infant_growth_log(
                user_id, infant_id, height_cm, weight_kg, head_cm,
                height_measured_at, weight_measured_at, head_measured_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, stored_infant_id, height_cm, weight_kg, head_cm, now, now, now, now),
        )
        return int(cursor.lastrowid)


def update_growth_record(
    *,
    user_id: str,
    growth_id: int,
    infant_id: int | None = None,
    height_cm: float | None,
    weight_kg: float | None,
    head_cm: float | None,
) -> bool:
    init_db()
    fields: list[str] = []
    params: list[Any] = []
    for name, value in (("height_cm", height_cm), ("weight_kg", weight_kg), ("head_cm", head_cm)):
        if value is not None:
            fields.append(f"{name} = ?")
            params.append(value)
    if not fields:
        return False
    now = _now()
    if height_cm is not None:
        fields.append("height_measured_at = ?")
        params.append(now)
    if weight_kg is not None:
        fields.append("weight_measured_at = ?")
        params.append(now)
    if head_cm is not None:
        fields.append("head_measured_at = ?")
        params.append(now)
    clauses = ["user_id = ?", "growth_id = ?"]
    params.extend([user_id, growth_id])
    if infant_id is not None:
        clauses.append("infant_id = ?")
        params.append(int(infant_id))
    with _connect() as conn:
        cursor = conn.execute(
            f"UPDATE infant_growth_log SET {', '.join(fields)} WHERE {' AND '.join(clauses)}",
            params,
        )
        return cursor.rowcount > 0


def list_growth_records(*, user_id: str, infant_id: int) -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        return [
            _row_dict(row)
            for row in conn.execute(
                "SELECT * FROM infant_growth_log WHERE user_id = ? AND infant_id = ? ORDER BY growth_id DESC",
                (user_id, infant_id),
            )
        ]


def latest_growth_record_for_user(user_id: str) -> dict[str, Any] | None:
    init_db()
    uid = str(user_id or "").strip()
    if not uid:
        return None
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM infant_growth_log
            WHERE user_id = ?
            ORDER BY growth_id DESC
            LIMIT 1
            """,
            (uid,),
        ).fetchone()
        return _row_dict(row) if row else None


def growth_records_for_user(user_id: str) -> list[dict[str, Any]]:
    init_db()
    uid = str(user_id or "").strip()
    if not uid:
        return []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM infant_growth_log
            WHERE user_id = ?
            ORDER BY growth_id DESC
            """,
            (uid,),
        ).fetchall()
        return [_row_dict(row) for row in rows]


def add_pumping_record(
    *,
    user_id: str,
    pump_time: str,
    pump_type: int,
    pump_milk_volum: float | None,
    pump_milk_duration: int | None = None,
    pump_source: int = 1,
    pump_title: str = "",
) -> int:
    init_db()
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO pumping_log(user_id, pump_start_time, pump_end_time, pump_milk_volum, pump_type, pump_milk_duration, pump_source, pump_title, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                pump_time,
                pump_time,
                pump_milk_volum,
                int(pump_type),
                pump_milk_duration,
                int(pump_source),
                str(pump_title or ""),
                _now(),
            ),
        )
        return int(cursor.lastrowid)


def list_pumping_records(*, user_id: str, start_at: str | None = None, end_at: str | None = None) -> list[dict[str, Any]]:
    init_db()
    clauses = ["user_id = ?"]
    params: list[Any] = [user_id]
    if start_at:
        clauses.append("pump_start_time >= ?")
        params.append(start_at)
    if end_at:
        clauses.append("pump_start_time <= ?")
        params.append(end_at)
    with _connect() as conn:
        return [
            _row_dict(row)
            for row in conn.execute(
                f"SELECT * FROM pumping_log WHERE {' AND '.join(clauses)} ORDER BY pump_start_time DESC",
                params,
            )
        ]


def get_pumping_record(pump_id: int) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM pumping_log WHERE pumping_id = ?", (pump_id,)).fetchone()
        return _row_dict(row) if row else None


def delete_pumping_record(*, user_id: str, pump_id: int) -> bool:
    init_db()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM pumping_log WHERE user_id = ? AND pumping_id = ?", (user_id, pump_id))
        return cursor.rowcount > 0


def upsert_pump_threshold(user_id: str, values: dict[str, int]) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO pump_threshold(user_id, stimulate_level_l, deep_level_l, stimulate_level_r, deep_level_r, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              stimulate_level_l = excluded.stimulate_level_l,
              deep_level_l = excluded.deep_level_l,
              stimulate_level_r = excluded.stimulate_level_r,
              deep_level_r = excluded.deep_level_r,
              updated_at = excluded.updated_at
            """,
            (
                user_id,
                values["stimulate_level_l"],
                values["deep_level_l"],
                values["stimulate_level_r"],
                values["deep_level_r"],
                _now(),
            ),
        )


def get_pump_threshold(user_id: str) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM pump_threshold WHERE user_id = ?", (user_id,)).fetchone()
        if row:
            return _row_dict(row)
    return {}


def upsert_pump_health(user_id: str, health_l: int, health_r: int) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO pump_health(user_id, health_l, health_r, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              health_l = excluded.health_l,
              health_r = excluded.health_r,
              updated_at = excluded.updated_at
            """,
            (user_id, health_l, health_r, _utc_now()),
        )


def get_pump_health(user_id: str) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM pump_health WHERE user_id = ?", (user_id,)).fetchone()
        return _row_dict(row) if row else None


def save_device_info(user_id: str, device_left: dict[str, Any], device_right: dict[str, Any], payload: dict[str, Any]) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO pump_device_info(user_id, device_left_json, device_right_json, payload_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              device_left_json = excluded.device_left_json,
              device_right_json = excluded.device_right_json,
              payload_json = excluded.payload_json,
              updated_at = excluded.updated_at
            """,
            (
                user_id,
                json.dumps(device_left, ensure_ascii=False),
                json.dumps(device_right, ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False),
                _now(),
            ),
        )


def get_device_info(user_id: str) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM pump_device_info WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return None
    payload = _loads(row["payload_json"])
    payload["updated_at"] = row["updated_at"]
    return payload


def record_workstate(user_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    init_db()
    latest = latest_workstate(user_id)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO pump_workstate_event(user_id, payload_json, created_at) VALUES (?, ?, ?)",
            (user_id, json.dumps(payload, ensure_ascii=False), _now()),
        )
    return latest


def latest_workstate(user_id: str) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT payload_json FROM pump_workstate_event WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return _loads(row["payload_json"]) if row else None


def add_pending_reply(user_id: str, mode: str, output: str, payload: dict[str, Any]) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO pump_workstate_pending_reply(user_id, mode, output, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, mode, output, json.dumps(payload, ensure_ascii=False), _now()),
        )


def pull_pending_replies(user_id: str, limit: int) -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM pump_workstate_pending_reply
            WHERE user_id = ? AND pulled_at IS NULL
            ORDER BY id ASC LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(f"UPDATE pump_workstate_pending_reply SET pulled_at = ? WHERE id IN ({placeholders})", [_now(), *ids])
    return [
        {
            "id": int(row["id"]),
            "mode": row["mode"],
            "output": row["output"],
            "created_at": row["created_at"],
            "payload": _loads(row["payload_json"]),
        }
        for row in rows
    ]


def record_pump_process(user_id: str, payload: dict[str, Any]) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO pump_process_point(user_id, payload_json, created_at) VALUES (?, ?, ?)",
            (user_id, json.dumps(payload, ensure_ascii=False), _now()),
        )


def get_pump_workstate_reply_state(user_id: str) -> dict[str, Any]:
    return _get_pump_reply_state("pump_workstate_reply_state", user_id)


def set_pump_workstate_reply_state(user_id: str, state: dict[str, Any] | None) -> None:
    _set_pump_reply_state("pump_workstate_reply_state", user_id, state)


def get_pump_process_reply_state(user_id: str) -> dict[str, Any]:
    return _get_pump_reply_state("pump_process_reply_state", user_id)


def set_pump_process_reply_state(user_id: str, state: dict[str, Any] | None) -> None:
    _set_pump_reply_state("pump_process_reply_state", user_id, state)


def _get_pump_reply_state(table_name: str, user_id: str) -> dict[str, Any]:
    init_db()
    uid = str(user_id or "").strip()
    if not uid:
        return {}
    with _connect() as conn:
        row = conn.execute(f"SELECT state_json FROM {table_name} WHERE user_id = ?", (uid,)).fetchone()
    return _loads(row["state_json"]) if row else {}


def _set_pump_reply_state(table_name: str, user_id: str, state: dict[str, Any] | None) -> None:
    init_db()
    uid = str(user_id or "").strip()
    if not uid:
        return
    payload = state if isinstance(state, dict) else {}
    with _connect() as conn:
        conn.execute(
            f"""
            INSERT INTO {table_name}(user_id, state_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
              state_json = excluded.state_json,
              updated_at = excluded.updated_at
            """,
            (uid, json.dumps(payload, ensure_ascii=False), _now()),
        )


def save_uploaded_file(metadata: dict[str, Any]) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO uploaded_file(file_id, original_name, extension, mime_type, size, path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                metadata["id"],
                metadata["name"],
                metadata["extension"],
                metadata["mime_type"],
                int(metadata["size"]),
                metadata["path"],
                int(metadata["created_at"]),
            ),
        )


def get_uploaded_file(file_id: str) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM uploaded_file WHERE file_id = ?", (file_id,)).fetchone()
        return _row_dict(row) if row else None


def save_care_plan_artifact(
    *,
    user_id: str,
    plan_type: str,
    title: str,
    summary: str,
    payload: dict[str, Any],
    source_artifact_type: str = "",
) -> dict[str, Any] | None:
    init_db()
    uid = str(user_id or "").strip()
    normalized_type = str(plan_type or "").strip()
    normalized_title = str(title or "").strip()
    if not uid or not normalized_type or not normalized_title or not isinstance(payload, dict):
        return None
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO care_plan_artifact(
                user_id, plan_type, title, summary, status, payload_json, source_artifact_type, updated_at
            )
            VALUES (?, ?, ?, ?, 'active', ?, ?, CURRENT_TIMESTAMP)
            """,
            (uid, normalized_type, normalized_title, str(summary or "").strip(), payload_json, str(source_artifact_type or "").strip()),
        )
        plan_id = int(cursor.lastrowid)
    return get_care_plan_artifact(user_id=uid, plan_id=plan_id)


def list_care_plan_artifacts(*, user_id: str, status: str = "active") -> list[dict[str, Any]]:
    init_db()
    uid = str(user_id or "").strip()
    normalized_status = str(status or "active").strip() or "active"
    if not uid:
        return []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT plan_id, user_id, plan_type, title, summary, status, payload_json,
                   source_artifact_type, created_at, updated_at
            FROM care_plan_artifact
            WHERE user_id = ? AND status = ?
            ORDER BY updated_at DESC, plan_id DESC
            """,
            (uid, normalized_status),
        ).fetchall()
    return [_care_plan_artifact_from_row(row) for row in rows]


def get_care_plan_artifact(*, user_id: str, plan_id: int) -> dict[str, Any] | None:
    init_db()
    uid = str(user_id or "").strip()
    try:
        pid = int(plan_id)
    except Exception:
        pid = 0
    if not uid or pid <= 0:
        return None
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT plan_id, user_id, plan_type, title, summary, status, payload_json,
                   source_artifact_type, created_at, updated_at
            FROM care_plan_artifact
            WHERE user_id = ? AND plan_id = ?
            """,
            (uid, pid),
        ).fetchone()
    return _care_plan_artifact_from_row(row) if row else None


def update_care_plan_artifact_payload(
    *,
    user_id: str,
    plan_id: int,
    payload: dict[str, Any],
    summary: str | None = None,
) -> dict[str, Any] | None:
    init_db()
    uid = str(user_id or "").strip()
    try:
        pid = int(plan_id)
    except Exception:
        pid = 0
    if not uid or pid <= 0 or not isinstance(payload, dict):
        return None
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    with _connect() as conn:
        if summary is None:
            cursor = conn.execute(
                """
                UPDATE care_plan_artifact
                SET payload_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND plan_id = ? AND status = 'active'
                """,
                (payload_json, uid, pid),
            )
        else:
            cursor = conn.execute(
                """
                UPDATE care_plan_artifact
                SET payload_json = ?, summary = ?, updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ? AND plan_id = ? AND status = 'active'
                """,
                (payload_json, str(summary or "").strip(), uid, pid),
            )
    if cursor.rowcount <= 0:
        return None
    return get_care_plan_artifact(user_id=uid, plan_id=pid)


def delete_care_plan_artifact(*, user_id: str, plan_id: int) -> bool:
    init_db()
    uid = str(user_id or "").strip()
    try:
        pid = int(plan_id)
    except Exception:
        pid = 0
    if not uid or pid <= 0:
        return False
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE care_plan_artifact
            SET status = 'deleted', updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND plan_id = ? AND status <> 'deleted'
            """,
            (uid, pid),
        )
    return cursor.rowcount > 0


def reset_birth_journey_care_plans_for_dev() -> int:
    """Hide saved birth journey plans on local demo startup."""
    init_db()
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE care_plan_artifact
            SET status = 'deleted', updated_at = CURRENT_TIMESTAMP
            WHERE plan_type = 'birth_journey' AND status = 'active'
            """
        )
        return int(cursor.rowcount or 0)


def reset_pregnancy_diary_for_dev() -> int:
    """Clear pregnancy diary records on local demo startup."""
    init_db()
    with _connect() as conn:
        notes = conn.execute("DELETE FROM pregnancy_diary_health_note")
        entries = conn.execute("DELETE FROM pregnancy_diary_entry")
        return int(notes.rowcount or 0) + int(entries.rowcount or 0)


def list_pregnancy_diary_entries(
    *,
    user_id: str,
    start_date: str = "",
    end_date: str = "",
    limit: int = 30,
) -> list[dict[str, Any]]:
    init_db()
    uid = str(user_id or "").strip()
    if not uid:
        return []
    safe_limit = min(max(int(limit or 30), 1), 100)
    clauses = ["user_id = ?"]
    args: list[Any] = [uid]
    if start_date:
        clauses.append("entry_date >= ?")
        args.append(str(start_date))
    if end_date:
        clauses.append("entry_date <= ?")
        args.append(str(end_date))
    args.append(safe_limit)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT entry_id, user_id, entry_date, gestational_week, mood, energy_level,
                   sleep_summary, fetal_movement, symptom_tags_json, appointment_note,
                   nutrition_note, content, attachments_json, created_at, updated_at
            FROM pregnancy_diary_entry
            WHERE {" AND ".join(clauses)}
            ORDER BY entry_date DESC, entry_id DESC
            LIMIT ?
            """,
            tuple(args),
        ).fetchall()
    return [_pregnancy_diary_entry_from_row(row) for row in rows]


def _demo_seed_has_run(*, user_id: str, seed_key: str) -> bool:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM demo_seed_state WHERE user_id = ? AND seed_key = ?",
            (str(user_id or "").strip(), str(seed_key or "").strip()),
        ).fetchone()
    return row is not None


def _mark_demo_seed(*, user_id: str, seed_key: str) -> None:
    init_db()
    uid = str(user_id or "").strip()
    key = str(seed_key or "").strip()
    if not uid or not key:
        return
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO demo_seed_state(user_id, seed_key) VALUES (?, ?)",
            (uid, key),
        )


def get_pregnancy_diary_entry(*, user_id: str, entry_id: int) -> dict[str, Any] | None:
    init_db()
    uid = str(user_id or "").strip()
    try:
        eid = int(entry_id)
    except Exception:
        eid = 0
    if not uid or eid <= 0:
        return None
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT entry_id, user_id, entry_date, gestational_week, mood, energy_level,
                   sleep_summary, fetal_movement, symptom_tags_json, appointment_note,
                   nutrition_note, content, attachments_json, created_at, updated_at
            FROM pregnancy_diary_entry
            WHERE user_id = ? AND entry_id = ?
            """,
            (uid, eid),
        ).fetchone()
    return _pregnancy_diary_entry_from_row(row) if row else None


def get_pregnancy_diary_entry_by_date(*, user_id: str, entry_date: str) -> dict[str, Any] | None:
    init_db()
    uid = str(user_id or "").strip()
    day = str(entry_date or "").strip()
    if not uid or not day:
        return None
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT entry_id, user_id, entry_date, gestational_week, mood, energy_level,
                   sleep_summary, fetal_movement, symptom_tags_json, appointment_note,
                   nutrition_note, content, attachments_json, created_at, updated_at
            FROM pregnancy_diary_entry
            WHERE user_id = ? AND entry_date = ?
            ORDER BY entry_id DESC
            LIMIT 1
            """,
            (uid, day),
        ).fetchone()
    return _pregnancy_diary_entry_from_row(row) if row else None


def save_pregnancy_diary_entry(
    *,
    user_id: str,
    entry_date: str,
    gestational_week: str = "",
    mood: str = "",
    energy_level: str = "",
    sleep_summary: str = "",
    fetal_movement: str = "",
    symptom_tags: list[str] | None = None,
    appointment_note: str = "",
    nutrition_note: str = "",
    content: str = "",
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    init_db()
    uid = str(user_id or "").strip()
    day = str(entry_date or "").strip()
    if not uid or not day:
        return None
    tags_json = json.dumps([str(tag).strip() for tag in (symptom_tags or []) if str(tag).strip()], ensure_ascii=False)
    attachments_json = json.dumps(attachments or [], ensure_ascii=False, sort_keys=True)
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO pregnancy_diary_entry(
                user_id, entry_date, gestational_week, mood, energy_level, sleep_summary,
                fetal_movement, symptom_tags_json, appointment_note, nutrition_note,
                content, attachments_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                uid,
                day,
                str(gestational_week or "").strip(),
                str(mood or "").strip(),
                str(energy_level or "").strip(),
                str(sleep_summary or "").strip(),
                str(fetal_movement or "").strip(),
                tags_json,
                str(appointment_note or "").strip(),
                str(nutrition_note or "").strip(),
                str(content or "").strip(),
                attachments_json,
            ),
        )
        entry_id = int(cursor.lastrowid)
    return get_pregnancy_diary_entry(user_id=uid, entry_id=entry_id)


def add_pregnancy_diary_health_note(
    *,
    user_id: str,
    entry_date: str,
    topic: str = "",
    user_report: str = "",
    asked_questions: list[str] | None = None,
    known_answers: list[str] | None = None,
    suggestion_summary: str = "",
    follow_up: str = "",
) -> dict[str, Any] | None:
    init_db()
    uid = str(user_id or "").strip()
    day = str(entry_date or "").strip()
    if not uid or not day:
        return None
    entry = get_pregnancy_diary_entry_by_date(user_id=uid, entry_date=day)
    if not entry:
        entry = save_pregnancy_diary_entry(user_id=uid, entry_date=day)
    if not entry:
        return None
    existing_note = _find_mergeable_pregnancy_diary_health_note(
        user_id=uid,
        entry_id=int(entry["entry_id"]),
        topic=topic,
    )
    asked_json = json.dumps([str(item).strip() for item in (asked_questions or []) if str(item).strip()], ensure_ascii=False)
    answers_json = json.dumps([str(item).strip() for item in (known_answers or []) if str(item).strip()], ensure_ascii=False)
    if existing_note:
        merged_asked = _merge_unique_strings(existing_note.get("asked_questions"), asked_questions)
        merged_answers = _merge_unique_strings(existing_note.get("known_answers"), known_answers)
        merged_user_report = _merge_distinct_text(existing_note.get("user_report"), user_report)
        merged_summary = _merge_distinct_text(existing_note.get("suggestion_summary"), suggestion_summary)
        merged_follow_up = _merge_distinct_text(existing_note.get("follow_up"), follow_up)
        with _connect() as conn:
            conn.execute(
                """
                UPDATE pregnancy_diary_health_note
                SET user_report = ?, asked_questions_json = ?, known_answers_json = ?,
                    suggestion_summary = ?, follow_up = ?
                WHERE user_id = ? AND note_id = ?
                """,
                (
                    merged_user_report,
                    json.dumps(merged_asked, ensure_ascii=False),
                    json.dumps(merged_answers, ensure_ascii=False),
                    merged_summary,
                    merged_follow_up,
                    uid,
                    int(existing_note["note_id"]),
                ),
            )
        note = get_pregnancy_diary_health_note(user_id=uid, note_id=int(existing_note["note_id"]))
        if note:
            note["mutation"] = "updated"
            note["merged_existing"] = True
        return note

    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO pregnancy_diary_health_note(
                entry_id, user_id, entry_date, topic, user_report, asked_questions_json,
                known_answers_json, suggestion_summary, follow_up
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(entry["entry_id"]),
                uid,
                day,
                str(topic or "").strip(),
                str(user_report or "").strip(),
                asked_json,
                answers_json,
                str(suggestion_summary or "").strip(),
                str(follow_up or "").strip(),
            ),
        )
        note_id = int(cursor.lastrowid)
    note = get_pregnancy_diary_health_note(user_id=uid, note_id=note_id)
    if note:
        note["mutation"] = "created"
        note["merged_existing"] = False
    return note


def _find_mergeable_pregnancy_diary_health_note(*, user_id: str, entry_id: int, topic: str) -> dict[str, Any] | None:
    normalized_topic = _normalize_health_note_topic(topic)
    if not normalized_topic:
        return None
    for note in list_pregnancy_diary_health_notes(user_id=user_id, entry_id=entry_id):
        if _normalize_health_note_topic(note.get("topic")) == normalized_topic:
            return note
    return None


def _normalize_health_note_topic(value: Any) -> str:
    text = str(value or "").strip().lower()
    return "".join(ch for ch in text if ch.isalnum() or "\u3400" <= ch <= "\u9fff")


def _merge_unique_strings(existing: Any, incoming: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in list(existing or []) + list(incoming or []):
        text = str(item or "").strip()
        key = " ".join(text.split())
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _merge_distinct_text(existing: Any, incoming: Any) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for raw in (existing, incoming):
        text = str(raw or "").strip()
        if not text:
            continue
        for piece in [line.strip() for line in text.splitlines() if line.strip()] or [text]:
            key = " ".join(piece.split())
            if not key or key in seen:
                continue
            seen.add(key)
            parts.append(piece)
    return "\n".join(parts)


def get_pregnancy_diary_health_note(*, user_id: str, note_id: int) -> dict[str, Any] | None:
    init_db()
    uid = str(user_id or "").strip()
    try:
        nid = int(note_id)
    except Exception:
        nid = 0
    if not uid or nid <= 0:
        return None
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT note_id, entry_id, user_id, entry_date, topic, user_report,
                   asked_questions_json, known_answers_json, suggestion_summary,
                   follow_up, created_at
            FROM pregnancy_diary_health_note
            WHERE user_id = ? AND note_id = ?
            """,
            (uid, nid),
        ).fetchone()
    return _pregnancy_diary_health_note_from_row(row) if row else None


def list_pregnancy_diary_health_notes(*, user_id: str, entry_id: int) -> list[dict[str, Any]]:
    init_db()
    uid = str(user_id or "").strip()
    try:
        eid = int(entry_id)
    except Exception:
        eid = 0
    if not uid or eid <= 0:
        return []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT note_id, entry_id, user_id, entry_date, topic, user_report,
                   asked_questions_json, known_answers_json, suggestion_summary,
                   follow_up, created_at
            FROM pregnancy_diary_health_note
            WHERE user_id = ? AND entry_id = ?
            ORDER BY note_id DESC
            """,
            (uid, eid),
        ).fetchall()
    return [_pregnancy_diary_health_note_from_row(row) for row in rows]


def update_pregnancy_diary_entry(
    *,
    user_id: str,
    entry_id: int,
    entry_date: str,
    gestational_week: str = "",
    mood: str = "",
    energy_level: str = "",
    sleep_summary: str = "",
    fetal_movement: str = "",
    symptom_tags: list[str] | None = None,
    appointment_note: str = "",
    nutrition_note: str = "",
    content: str = "",
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    init_db()
    uid = str(user_id or "").strip()
    try:
        eid = int(entry_id)
    except Exception:
        eid = 0
    day = str(entry_date or "").strip()
    if not uid or eid <= 0 or not day:
        return None
    tags_json = json.dumps([str(tag).strip() for tag in (symptom_tags or []) if str(tag).strip()], ensure_ascii=False)
    attachments_json = json.dumps(attachments or [], ensure_ascii=False, sort_keys=True)
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE pregnancy_diary_entry
            SET entry_date = ?, gestational_week = ?, mood = ?, energy_level = ?,
                sleep_summary = ?, fetal_movement = ?, symptom_tags_json = ?,
                appointment_note = ?, nutrition_note = ?, content = ?,
                attachments_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND entry_id = ?
            """,
            (
                day,
                str(gestational_week or "").strip(),
                str(mood or "").strip(),
                str(energy_level or "").strip(),
                str(sleep_summary or "").strip(),
                str(fetal_movement or "").strip(),
                tags_json,
                str(appointment_note or "").strip(),
                str(nutrition_note or "").strip(),
                str(content or "").strip(),
                attachments_json,
                uid,
                eid,
            ),
        )
        if cursor.rowcount <= 0:
            return None
    return get_pregnancy_diary_entry(user_id=uid, entry_id=eid)


def delete_pregnancy_diary_entry(*, user_id: str, entry_id: int) -> bool:
    init_db()
    uid = str(user_id or "").strip()
    try:
        eid = int(entry_id)
    except Exception:
        eid = 0
    if not uid or eid <= 0:
        return False
    with _connect() as conn:
        conn.execute(
            "DELETE FROM pregnancy_diary_health_note WHERE user_id = ? AND entry_id = ?",
            (uid, eid),
        )
        cursor = conn.execute(
            "DELETE FROM pregnancy_diary_entry WHERE user_id = ? AND entry_id = ?",
            (uid, eid),
        )
    return cursor.rowcount > 0


def query_plan_tasks(*, user_id: str, target_date: str) -> dict[str, Any] | None:
    init_db()
    uid = str(user_id or "").strip()
    date_text = str(target_date or "").strip()
    if not uid or not date_text:
        return None
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT item_id, user_id, plan_id, date, task_id, start_time, end_time,
                   content, type, source, is_milk_pump, finish
            FROM calendar
            WHERE user_id = ? AND date = ?
            ORDER BY COALESCE(start_time, ''), COALESCE(task_id, item_id), item_id
            """,
            (uid, date_text),
        ).fetchall()
        plan_id = _first_plan_id(rows)
        plan_row = None
        if plan_id is not None:
            plan_row = conn.execute(
                "SELECT plan_type FROM milk_plan WHERE user_id = ? AND plan_id = ?",
                (uid, plan_id),
            ).fetchone()
    return {
        "plan_type": _api_plan_type(plan_row["plan_type"] if plan_row else None, plan_id),
        "task_list": [_api_task_from_calendar_row(row) for row in rows],
    }


def list_calendar_tasks_for_notify(*, user_id: str, target_date: str) -> list[dict[str, Any]]:
    init_db()
    uid = str(user_id or "").strip()
    date_text = str(target_date or "").strip()
    if not uid or not date_text:
        return []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT item_id, user_id, plan_id, date, task_id, start_time, end_time,
                   content, type, source, is_milk_pump, finish
            FROM calendar
            WHERE user_id = ? AND date = ?
            ORDER BY COALESCE(start_time, ''), COALESCE(task_id, item_id), item_id
            """,
            (uid, date_text),
        ).fetchall()
        return [_row_dict(row) for row in rows]


def has_notify_task_result(*, user_id: str, task: dict[str, Any], as_of_time: str | None = None) -> bool:
    init_db()
    uid = str(user_id or "").strip()
    start_time = str(task.get("start_time") or "").strip()
    if not uid or not start_time:
        return False

    finish = str(task.get("finish") or "").strip().lower()
    if finish == "true":
        return True

    end_time = str(as_of_time or task.get("end_time") or start_time).strip()
    if end_time < start_time:
        end_time = start_time

    task_type = str(task.get("type") or "").strip()
    is_milk_pump = str(task.get("is_milk_pump") or "").strip().lower() in {"1", "true"}
    with _connect() as conn:
        if is_milk_pump or task_type == "吸奶":
            row = conn.execute(
                """
                SELECT 1
                FROM pumping_log
                WHERE user_id = ? AND pump_start_time >= ? AND pump_start_time <= ?
                LIMIT 1
                """,
                (uid, start_time, end_time),
            ).fetchone()
            return row is not None

        if task_type == "亲喂":
            row = conn.execute(
                """
                SELECT 1
                FROM feeding_log
                WHERE user_id = ? AND feed_type = ? AND feed_time >= ? AND feed_time <= ?
                LIMIT 1
                """,
                (uid, FEED_TYPE_CODE_TO_TEXT[0], start_time, end_time),
            ).fetchone()
            if row is not None:
                return True
            row = conn.execute(
                """
                SELECT 1
                FROM pumping_log
                WHERE user_id = ? AND pump_type = 2 AND pump_start_time >= ? AND pump_start_time <= ?
                LIMIT 1
                """,
                (uid, start_time, end_time),
            ).fetchone()
            return row is not None

        if task_type in {"喂养", "瓶喂", "配方奶"}:
            row = conn.execute(
                """
                SELECT 1
                FROM feeding_log
                WHERE user_id = ? AND feed_time >= ? AND feed_time <= ?
                LIMIT 1
                """,
                (uid, start_time, end_time),
            ).fetchone()
            return row is not None

    return False


def has_task_result(*, user_id: str, task: dict[str, Any], as_of_time: str | None = None) -> bool:
    return has_notify_task_result(user_id=user_id, task=task, as_of_time=as_of_time)


def latest_infant_profile_for_user(user_id: str) -> dict[str, Any] | None:
    init_db()
    uid = str(user_id or "").strip()
    if not uid:
        return None
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM infant_profile
            WHERE user_id = ?
            ORDER BY infant_id DESC
            LIMIT 1
            """,
            (uid,),
        ).fetchone()
        return _row_dict(row) if row else None


def add_plan_tasks(*, user_id: str, target_date: str, task_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    init_db()
    uid = str(user_id or "").strip()
    date_text = str(target_date or "").strip()
    if not uid or not date_text or not isinstance(task_list, list):
        return []

    inserted: list[dict[str, Any]] = []
    with _connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(task_id), 0) AS max_task_id FROM calendar WHERE user_id = ? AND date = ?",
            (uid, date_text),
        ).fetchone()
        next_task_id = int(row["max_task_id"] or 0) + 1
        for item in task_list:
            if not isinstance(item, dict):
                continue
            task_time = _hhmm(item.get("task_time"))
            task_content = str(item.get("task_content") or "").strip()
            if not task_time or not task_content:
                continue
            task_type = _request_task_type(item.get("task_type"))
            task_source = _request_task_source(item.get("task_source"))
            start_time = f"{date_text} {task_time}:00"
            cursor = conn.execute(
                """
                INSERT INTO calendar(
                    user_id, plan_id, date, task_id, start_time, end_time,
                    content, type, source, is_milk_pump, finish, created_at, modified_at
                )
                VALUES (?, NULL, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uid,
                    date_text,
                    next_task_id,
                    start_time,
                    task_content,
                    task_type["calendar_type"],
                    task_source,
                    task_type["is_milk_pump"],
                    "false",
                    _now(),
                    _now(),
                ),
            )
            inserted_row = conn.execute(
                """
                SELECT item_id, user_id, plan_id, date, task_id, start_time, end_time,
                       content, type, source, is_milk_pump, finish
                FROM calendar
                WHERE item_id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()
            if inserted_row:
                inserted.append(_api_task_from_calendar_row(inserted_row))
            next_task_id += 1
        conn.commit()
    return inserted


def delete_plan_task(*, user_id: str, target_date: str, task_id: int) -> bool:
    init_db()
    uid = str(user_id or "").strip()
    date_text = str(target_date or "").strip()
    if not uid or not date_text or int(task_id or 0) <= 0:
        return False
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM calendar WHERE user_id = ? AND date = ? AND task_id = ?",
            (uid, date_text, int(task_id)),
        )
        conn.commit()
        return int(cursor.rowcount or 0) > 0


def revise_plan_task(
    *,
    user_id: str,
    target_date: str,
    task_id: int,
    task_time: str,
    task_content: str,
    task_done: Any,
) -> bool:
    init_db()
    uid = str(user_id or "").strip()
    date_text = str(target_date or "").strip()
    time_text = _hhmm(task_time)
    content = str(task_content or "").strip()
    finish = _request_task_done(task_done)
    if not uid or not date_text or int(task_id or 0) <= 0 or not time_text or not content or finish is None:
        return False
    with _connect() as conn:
        current = conn.execute(
            """
            SELECT item_id, user_id, date, task_id, start_time, end_time,
                   content, type, source, is_milk_pump, finish
            FROM calendar
            WHERE user_id = ? AND date = ? AND task_id = ?
            """,
            (uid, date_text, int(task_id)),
        ).fetchone()
        if current is None:
            return False
        cursor = conn.execute(
            """
            UPDATE calendar
            SET start_time = ?,
                content = ?,
                finish = ?,
                modified_at = ?
            WHERE user_id = ? AND date = ? AND task_id = ?
            """,
            (f"{date_text} {time_text}:00", content, finish, _now(), uid, date_text, int(task_id)),
        )
        updated = conn.execute(
            """
            SELECT item_id, user_id, date, task_id, start_time, end_time,
                   content, type, source, is_milk_pump, finish
            FROM calendar
            WHERE user_id = ? AND date = ? AND task_id = ?
            """,
            (uid, date_text, int(task_id)),
        ).fetchone()
        if _is_finish_true(finish):
            _sync_completed_calendar_item_logs(conn, updated)
        else:
            _delete_completed_calendar_item_logs(conn, updated or current)
        conn.commit()
        return int(cursor.rowcount or 0) > 0


def sync_completed_calendar_item_logs(*, user_id: str, item_id: int) -> dict[str, int]:
    init_db()
    uid = str(user_id or "").strip()
    if not uid or int(item_id or 0) <= 0:
        return {"pumping_id": 0, "feeding_id": 0}
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT item_id, user_id, date, task_id, start_time, end_time,
                   content, type, source, is_milk_pump, finish
            FROM calendar
            WHERE user_id = ? AND item_id = ?
            """,
            (uid, int(item_id)),
        ).fetchone()
        result = _sync_completed_calendar_item_logs(conn, row)
        conn.commit()
        return result


def _sync_completed_calendar_item_logs(conn: sqlite3.Connection, row: sqlite3.Row | None) -> dict[str, int]:
    if row is None or not _is_finish_true(row["finish"]):
        return {"pumping_id": 0, "feeding_id": 0}
    item_type = str(row["type"] or "").strip()
    is_pump = str(row["is_milk_pump"]) in {"1", "true", "True"} or item_type == "吸奶"
    is_nursing = item_type == "亲喂"
    if not is_pump and not is_nursing:
        return {"pumping_id": 0, "feeding_id": 0}

    user_id = str(row["user_id"] or "").strip()
    start_time = str(row["start_time"] or "").strip()
    end_time = str(row["end_time"] or "").strip() or start_time
    content = str(row["content"] or "").strip()
    if not user_id or not start_time:
        return {"pumping_id": 0, "feeding_id": 0}

    if is_nursing:
        feeding_id = _ensure_calendar_feeding_log(conn, user_id=user_id, feed_time=start_time, duration_minutes=_duration_minutes(start_time, end_time), title=content)
        return {"pumping_id": 0, "feeding_id": feeding_id}

    pumping_id = _ensure_calendar_pumping_log(
        conn,
        user_id=user_id,
        pump_time=start_time,
        pump_end_time=end_time,
        pump_type=0,
        milk_ml=0.0,
        duration_minutes=_duration_minutes(start_time, end_time),
        title=content or "吸奶",
    )
    return {"pumping_id": pumping_id, "feeding_id": 0}


def _delete_completed_calendar_item_logs(conn: sqlite3.Connection, row: sqlite3.Row | None) -> dict[str, int]:
    if row is None:
        return {"deleted_pumping": 0, "deleted_feeding": 0}
    item_type = str(row["type"] or "").strip()
    is_pump = str(row["is_milk_pump"]) in {"1", "true", "True"} or item_type == "吸奶"
    is_nursing = item_type == "亲喂"
    if not is_pump and not is_nursing:
        return {"deleted_pumping": 0, "deleted_feeding": 0}

    user_id = str(row["user_id"] or "").strip()
    start_time = str(row["start_time"] or "").strip()
    content = str(row["content"] or "").strip()
    if not user_id or not start_time:
        return {"deleted_pumping": 0, "deleted_feeding": 0}

    deleted_pumping = 0
    deleted_feeding = 0
    if is_nursing:
        deleted_feeding = _delete_calendar_feeding_log(conn, user_id=user_id, feed_time=start_time, title=content)
        deleted_pumping = _delete_calendar_pumping_log(conn, user_id=user_id, pump_time=start_time, pump_type=2, title=content or "亲喂")
        return {"deleted_pumping": deleted_pumping, "deleted_feeding": deleted_feeding}

    deleted_pumping = _delete_calendar_pumping_log(conn, user_id=user_id, pump_time=start_time, pump_type=0, title=content or "吸奶")
    return {"deleted_pumping": deleted_pumping, "deleted_feeding": 0}


def _ensure_calendar_pumping_log(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    pump_time: str,
    pump_end_time: str,
    pump_type: int,
    milk_ml: float | None,
    duration_minutes: int | None,
    title: str,
) -> int:
    existing = conn.execute(
        """
        SELECT pumping_id
        FROM pumping_log
        WHERE user_id = ?
          AND pump_start_time = ?
          AND COALESCE(pump_title, '') = ?
          AND pump_source = 2
          AND (? != 0 OR pump_type IN (0, 1))
          AND (? = 0 OR pump_type = ?)
        ORDER BY pumping_id DESC
        LIMIT 1
        """,
        (user_id, pump_time, str(title or ""), int(pump_type), int(pump_type), int(pump_type)),
    ).fetchone()
    if existing:
        return int(existing["pumping_id"] or 0)
    stored_pump_type = 1 if int(pump_type) == 0 else int(pump_type)
    cursor = conn.execute(
        """
        INSERT INTO pumping_log(user_id, pump_start_time, pump_end_time, pump_milk_volum,
                                pump_type, pump_milk_duration, pump_source, pump_title, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, pump_time, pump_end_time or pump_time, milk_ml, stored_pump_type, duration_minutes, 2, str(title or ""), _now()),
    )
    return int(cursor.lastrowid or 0)


def _delete_calendar_pumping_log(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    pump_time: str,
    pump_type: int,
    title: str,
) -> int:
    cursor = conn.execute(
        """
        DELETE FROM pumping_log
        WHERE user_id = ?
          AND pump_start_time = ?
          AND pump_source = 2
          AND COALESCE(pump_title, '') = ?
          AND (? != 0 OR pump_type IN (0, 1))
          AND (? = 0 OR pump_type = ?)
        """,
        (user_id, pump_time, str(title or ""), int(pump_type), int(pump_type), int(pump_type)),
    )
    return int(cursor.rowcount or 0)


def _ensure_calendar_feeding_log(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    feed_time: str,
    duration_minutes: int | None,
    title: str,
) -> int:
    infant = conn.execute(
        "SELECT infant_id FROM infant_profile WHERE user_id = ? ORDER BY infant_id LIMIT 1",
        (user_id,),
    ).fetchone()
    infant_id = int(infant["infant_id"] or 0) if infant else 0
    if infant_id <= 0:
        return 0
    duration = int(duration_minutes or 0)
    existing = conn.execute(
        """
        SELECT feeding_id
        FROM feeding_log
        WHERE user_id = ?
          AND feed_time = ?
          AND feed_type = ?
          AND feed_action = 1
          AND COALESCE(feeding_title, '') = ?
        ORDER BY feeding_id DESC
        LIMIT 1
        """,
        (user_id, feed_time, FEED_TYPE_CODE_TO_TEXT[0], str(title or "")),
    ).fetchone()
    if existing:
        return int(existing["feeding_id"] or 0)
    cursor = conn.execute(
        """
        INSERT INTO feeding_log(user_id, infant_id, feed_time, feed_type, feed_milk_volum,
                                feed_action, feeding_title, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, infant_id, feed_time, FEED_TYPE_CODE_TO_TEXT[0], float(duration), 1, str(title or ""), _now()),
    )
    return int(cursor.lastrowid or 0)


def _delete_calendar_feeding_log(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    feed_time: str,
    title: str,
) -> int:
    cursor = conn.execute(
        """
        DELETE FROM feeding_log
        WHERE user_id = ?
          AND feed_time = ?
          AND feed_type = ?
          AND feed_action = 1
          AND COALESCE(feeding_title, '') = ?
        """,
        (user_id, feed_time, FEED_TYPE_CODE_TO_TEXT[0], str(title or "")),
    )
    return int(cursor.rowcount or 0)


def _duration_minutes(start_time: Any, end_time: Any) -> int | None:
    try:
        start = datetime.fromisoformat(str(start_time))
        end = datetime.fromisoformat(str(end_time))
    except Exception:
        return None
    minutes = int((end - start).total_seconds() / 60)
    return minutes if minutes > 0 else None


def _is_finish_true(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "done", "completed"}


def pump_info(user_id: str) -> dict[str, Any]:
    uid = str(user_id or "").strip()
    if not uid:
        return {"error": -1, "lactation_info_list": []}
    with _connect() as conn:
        profile = conn.execute("SELECT delivery_date FROM user_profile WHERE user_id = ?", (uid,)).fetchone()
        # Per-day direct-breastfeeding (亲喂) session counts feed the
        # `total_milk_estimate` line on the trend chart so it includes the
        # baby's estimated direct intake on top of measured pump yield.
        breast_rows = conn.execute(
            """
            SELECT substr(feed_time, 1, 10) AS feed_day, COUNT(*) AS breast_count
            FROM feeding_log
            WHERE user_id = ? AND feed_type = ?
            GROUP BY feed_day
            """,
            (uid, FEED_TYPE_CODE_TO_TEXT[0]),
        ).fetchall()
    babies = list_baby_profiles(user_id)
    reference_date = (profile["delivery_date"] if profile else "") or (babies[0]["birth_date"] if babies else "")
    reference_day = _parse_date(reference_date)
    records = list_pumping_records(user_id=user_id)
    by_day: dict[str, float] = {}
    for record in records:
        date_key = str(record.get("pump_start_time", ""))[:10]
        if not date_key:
            continue
        try:
            by_day[date_key] = by_day.get(date_key, 0.0) + float(record.get("pump_milk_volum") or 0)
        except Exception:
            pass
    breast_by_day: dict[str, int] = {}
    for row in breast_rows:
        day_key = str(row["feed_day"] or "")
        if not day_key:
            continue
        try:
            breast_by_day[day_key] = int(row["breast_count"] or 0)
        except Exception:
            pass
    # Use the existing per-session estimate (driven by recent bottle-feed
    # history). When the user has no bottle data it returns None, which keeps
    # the legacy behavior `total_milk_estimate == total_milk` intact.
    breast_per_session_ml = estimate_breastfeeding_milk(user_id=uid) or 0.0
    today = datetime.now().date()
    lactation_info_list = []
    # Return one extra day because the status chart intentionally excludes
    # today; this keeps the 30-day view from losing its oldest day.
    for offset in range(30, -1, -1):
        day = today - timedelta(days=offset)
        date_key = day.isoformat()
        total = int(round(by_day.get(date_key, 0.0)))
        breast_count = breast_by_day.get(date_key, 0)
        estimated_breast_ml = int(round(breast_count * breast_per_session_ml))
        total_with_breast = max(total, 0) + max(estimated_breast_ml, 0)
        reference = None
        if reference_day is not None:
            reference = get_yield_reference_range((day - reference_day).days + 1)
        lactation_info_list.append(
            {
                "total_milk": total,
                "total_milk_estimate": total_with_breast,
                "reference_upper": int(round(float(reference.get("p85") or 0))) if reference else 0,
                "reference_lower": int(round(float(reference.get("p15") or 0))) if reference else 0,
                "delivery_date": f"{day.month}/{day.day}",
            }
        )
    return {"error": 0, "lactation_info_list": lactation_info_list}


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, factory=_ClosingConnection)
    conn.row_factory = sqlite3.Row
    return conn


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _normalize_display_name(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > 40:
        text = text[:40].strip()
    return text


def _normalize_birth_prep_memory_text(value: Any) -> str:
    if isinstance(value, list):
        text = "、".join(str(item).strip() for item in value if str(item or "").strip())
    elif isinstance(value, dict):
        text = "、".join(str(item).strip() for item in value.values() if str(item or "").strip())
    else:
        text = str(value or "").strip()
    text = text.strip(" \t\r\n，,。.")
    if not text or text.lower() in {"none", "n/a", "null", "unknown"}:
        return ""
    if text in {"待确认", "不确定", "还没确定", "未知", "无"}:
        return ""
    if len(text) > 80:
        text = text[:80].strip()
    return text


def _profile_age_value(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        age = int(value)
    except Exception:
        return None
    if age < 0 or age > 120:
        return None
    return age


def _loads(raw: Any) -> dict[str, Any]:
    try:
        loaded = json.loads(raw or "{}")
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _care_plan_artifact_from_row(row: Any) -> dict[str, Any]:
    payload = _loads(row["payload_json"])
    return {
        "plan_id": int(row["plan_id"] or 0),
        "user_id": str(row["user_id"] or ""),
        "plan_type": str(row["plan_type"] or ""),
        "title": str(row["title"] or ""),
        "summary": str(row["summary"] or ""),
        "status": str(row["status"] or ""),
        "source_artifact_type": str(row["source_artifact_type"] or ""),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "payload": payload,
    }


def _loads_list(raw: Any) -> list[Any]:
    try:
        loaded = json.loads(raw or "[]")
    except Exception:
        return []
    return loaded if isinstance(loaded, list) else []


def _pregnancy_diary_entry_from_row(row: Any) -> dict[str, Any]:
    entry_id = int(row["entry_id"] or 0)
    user_id = str(row["user_id"] or "")
    return {
        "entry_id": entry_id,
        "user_id": user_id,
        "entry_date": str(row["entry_date"] or ""),
        "gestational_week": str(row["gestational_week"] or ""),
        "mood": str(row["mood"] or ""),
        "energy_level": str(row["energy_level"] or ""),
        "sleep_summary": str(row["sleep_summary"] or ""),
        "fetal_movement": str(row["fetal_movement"] or ""),
        "symptom_tags": [str(item) for item in _loads_list(row["symptom_tags_json"]) if str(item).strip()],
        "appointment_note": str(row["appointment_note"] or ""),
        "nutrition_note": str(row["nutrition_note"] or ""),
        "content": str(row["content"] or ""),
        "attachments": [item for item in _loads_list(row["attachments_json"]) if isinstance(item, dict)],
        "health_notes": list_pregnancy_diary_health_notes(user_id=user_id, entry_id=entry_id),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def _pregnancy_diary_health_note_from_row(row: Any) -> dict[str, Any]:
    return {
        "note_id": int(row["note_id"] or 0),
        "entry_id": int(row["entry_id"] or 0),
        "user_id": str(row["user_id"] or ""),
        "entry_date": str(row["entry_date"] or ""),
        "topic": str(row["topic"] or ""),
        "user_report": str(row["user_report"] or ""),
        "asked_questions": [str(item) for item in _loads_list(row["asked_questions_json"]) if str(item).strip()],
        "known_answers": [str(item) for item in _loads_list(row["known_answers_json"]) if str(item).strip()],
        "suggestion_summary": str(row["suggestion_summary"] or ""),
        "follow_up": str(row["follow_up"] or ""),
        "created_at": str(row["created_at"] or ""),
    }


def _first_plan_id(rows: list[Any]) -> int | None:
    for row in rows:
        try:
            plan_id = int(row["plan_id"] or 0)
        except Exception:
            plan_id = 0
        if plan_id > 0:
            return plan_id
    return None


def _api_plan_type(raw_plan_type: Any, plan_id: int | None) -> str:
    if plan_id is None:
        return "None"
    token = str(raw_plan_type or "").strip().lower()
    mapping = {
        "maintain_milk": "maintain",
        "maintain": "maintain",
        "increase_milk": "chase",
        "increase": "chase",
        "chase": "chase",
        "decrease_milk": "wean",
        "decrease": "wean",
        "wean": "wean",
        "fertility": "fertility",
        "fertility_plan": "fertility",
    }
    return mapping.get(token, "None")


def _api_task_from_calendar_row(row: Any) -> dict[str, Any]:
    return {
        "task_id": int(row["task_id"] or row["item_id"] or 0),
        "task_time": _hhmm(row["start_time"]),
        "task_content": str(row["content"] or ""),
        "task_type": _api_task_type(row["type"], row["is_milk_pump"]),
        "task_source": _api_task_source(row["source"]),
        "task_done": _api_task_done(row["finish"]),
    }


def _api_task_type(raw_type: Any, is_milk_pump: Any) -> int:
    token = str(raw_type or "").strip().lower()
    if str(is_milk_pump) in {"1", "true", "True"} or token in {"pump", "吸奶", "pumping", "milk_pump"}:
        return 0
    if token in {"nursing", "breastfeeding", "direct", "亲喂"}:
        return 1
    return 2


def _request_task_type(raw_type: Any) -> dict[str, Any]:
    try:
        parsed = int(raw_type)
    except Exception:
        parsed = 2
    if parsed == 0:
        return {"calendar_type": "吸奶", "is_milk_pump": 1}
    if parsed == 1:
        return {"calendar_type": "亲喂", "is_milk_pump": 0}
    return {"calendar_type": "自定义", "is_milk_pump": 0}


def _api_task_source(raw_source: Any) -> str:
    token = str(raw_source or "").strip()
    if token in {"系统生成", "system", "System", "Mai", "AI", "ai"}:
        return "Mai"
    return "手动"


def _request_task_source(raw_source: Any) -> str:
    token = str(raw_source or "").strip()
    if token in {"Mai", "系统生成", "system", "System", "AI", "ai"}:
        return "系统生成"
    return "用户输入"


def _api_task_done(raw_finish: Any) -> str | bool:
    token = str(raw_finish).strip().lower()
    if token == "jump":
        return "jump"
    if token in {"1", "true", "yes", "done"}:
        return "true"
    return "false"


def _request_task_done(raw_finish: Any) -> str | None:
    if isinstance(raw_finish, bool):
        return "true" if raw_finish else "false"
    token = str(raw_finish).strip().lower()
    if token == "jump":
        return "jump"
    if token in {"1", "true", "yes", "done", "completed"}:
        return "true"
    if token in {"0", "false", "no", "pending", "not_done", "unfinished"}:
        return "false"
    return "false"


def _hhmm(value: Any) -> str:
    token = str(value or "").strip()
    if not token:
        return ""
    try:
        return datetime.fromisoformat(token).strftime("%H:%M")
    except Exception:
        pass
    if len(token) >= 5 and token[2] == ":":
        return token[:5]
    if " " in token:
        tail = token.split(" ")[-1]
        return tail[:5] if len(tail) >= 5 else tail
    return token


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _parse_date(value: Any) -> datetime.date | None:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        return datetime.fromisoformat(token).date()
    except Exception:
        return None


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


init_db()
