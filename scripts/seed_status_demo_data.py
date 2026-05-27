#!/usr/bin/env python3
"""Seed status overview demo data for lactation and baby growth charts."""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from momcozy_agent.services import data_store  # noqa: E402
from momcozy_agent.services.milk_management.assessment import get_yield_reference_range  # noqa: E402


USER_ID = "demo_mama_increase_001"
INFANT_ID = 18
BASE_TODAY = datetime(2026, 5, 21)
BASE_DELIVERY_DATE = datetime(2026, 3, 1)
PLAN_HISTORY_DAYS = 30
PLAN_PUMP_DURATION_MINUTES = 20
NURSING_ESTIMATE_ML = 45.0


def _resolve_today() -> datetime:
    raw = os.getenv("STATUS_DEMO_TODAY", "").strip()
    if raw:
        parsed = datetime.fromisoformat(raw)
        return datetime.combine(parsed.date(), datetime.min.time())
    return datetime.combine(datetime.now().date(), datetime.min.time())


TODAY = _resolve_today()
SHIFT_DAYS = (TODAY.date() - BASE_TODAY.date()).days
DELIVERY_DATE = (BASE_DELIVERY_DATE + timedelta(days=SHIFT_DAYS)).date().isoformat()


def _shift_timestamp(value: str) -> str:
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    return (parsed + timedelta(days=SHIFT_DAYS)).strftime("%Y-%m-%d %H:%M:%S")


# Weight curve starts inside the WHO girls P25-P50 band, then visibly
# decelerates over the last ~6 weeks, drifting down to the P10 line.
# A percentile drop like this is a clear clinical red flag in pediatrics
# (crossing two major centiles downward) and turns the chart into a
# textbook "milk supply -> baby intake -> weight stall" story that
# justifies a lactation-boost (追奶) plan.
GROWTH_POINTS = [
    (_shift_timestamp("2026-03-01 09:20:00"), 3.00, 49.0, 34.0),  # birth, ~P25
    (_shift_timestamp("2026-03-15 09:25:00"), 3.55, 52.0, 35.5),  # 2 wk, ~P25-P50 (39 g/d)
    (_shift_timestamp("2026-03-29 09:30:00"), 4.05, 54.5, 36.6),  # 4 wk, ~P25-P50 (36 g/d)
    (_shift_timestamp("2026-04-12 09:40:00"), 4.40, 56.0, 37.3),  # 6 wk, slipping toward P25 (25 g/d)
    (_shift_timestamp("2026-04-26 09:50:00"), 4.55, 57.0, 37.7),  # 8 wk, ~P15-P25 (11 g/d, slow)
    (_shift_timestamp("2026-05-10 09:50:00"), 4.70, 57.8, 38.0),  # 10 wk, ~P10-P15 (11 g/d, persistent slow)
    (_shift_timestamp("2026-05-17 09:40:00"), 4.78, 58.1, 38.1),  # 11 wk, ~P10 line (11 g/d)
    (_shift_timestamp("2026-05-21 09:21:00"), 4.85, 58.3, 38.2),  # 11.4 wk, on P10 (17 g/d, still under-grown)
]


SESSION_TIMES = [
    ("06:00", "吸奶", 0.13),
    ("08:15", "吸奶", 0.13),
    ("10:30", "吸奶", 0.13),
    ("12:45", "吸奶", 0.13),
    ("15:00", "吸奶", 0.12),
    ("17:15", "吸奶", 0.12),
    ("19:30", "吸奶", 0.12),
    ("21:45", "吸奶", 0.12),
]

NURSING_TIMES = [
    ("07:20", "早间亲喂"),
    ("11:45", "午间亲喂"),
    ("16:30", "傍晚亲喂"),
    ("20:45", "睡前亲喂"),
]


def _connect() -> sqlite3.Connection:
    data_store.init_db()
    conn = sqlite3.connect(data_store.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_profiles(conn: sqlite3.Connection) -> None:
    now = TODAY.strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT INTO user_profile(
            user_id, user_nickname, delivery_date, lactation_advice, feeding_advice,
            daily_summary, updated_at, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            user_nickname = excluded.user_nickname,
            delivery_date = excluded.delivery_date,
            lactation_advice = excluded.lactation_advice,
            feeding_advice = excluded.feeding_advice,
            daily_summary = excluded.daily_summary,
            updated_at = excluded.updated_at
        """,
        (
            USER_ID,
            "Demo Mama",
            DELIVERY_DATE,
            "近 30 天吸乳记录稳定在每日 8 次；加入 2-4 次亲喂估算后，日奶量多数略低于 P15 下沿，提示供给接近但仍未完全达到参考区间。建议制定温和追奶计划，并结合宝宝体重增长继续观察。",
            "宝宝近 4-6 周体重增长放缓，当前估算摄入略低于参考区间下沿，需要继续关注摄入、尿量和体重曲线。建议与儿科或泌乳顾问沟通，并启动温和追奶方案。",
            "每日吸奶节律稳定，含亲喂估算后的奶量接近 P15 下沿但仍略低，适合生成追奶计划并继续跟踪宝宝成长。",
            now,
            now,
        ),
    )
    conn.execute(
        """
        INSERT INTO infant_profile(
            infant_id, user_id, user_nickname, infant_name, sex, birth_date, updated_at, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(infant_id) DO UPDATE SET
            user_id = excluded.user_id,
            user_nickname = excluded.user_nickname,
            infant_name = excluded.infant_name,
            sex = excluded.sex,
            birth_date = excluded.birth_date,
            updated_at = excluded.updated_at
        """,
        (INFANT_ID, USER_ID, "Demo Mama", "Demo Baby", "girls", DELIVERY_DATE, now, now),
    )


def _seed_growth(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM infant_growth_log WHERE user_id = ?", (USER_ID,))
    for measured_at, weight_kg, height_cm, head_cm in GROWTH_POINTS:
        conn.execute(
            """
            INSERT INTO infant_growth_log(
                user_id, infant_id, height_cm, weight_kg, head_cm,
                height_measured_at, weight_measured_at, head_measured_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                USER_ID,
                INFANT_ID,
                height_cm,
                weight_kg,
                head_cm,
                measured_at,
                measured_at,
                measured_at,
                measured_at,
            ),
        )


def _daily_total_for(day: datetime, index: int) -> int:
    # Pumped milk plus estimated nursing should sit just below P15. That keeps
    # the demo in a realistic "needs gentle catch-up" state without making the
    # daily output look implausibly far below the reference band.
    postpartum_day = (day.date() - datetime.fromisoformat(DELIVERY_DATE).date()).days + 1
    ref = get_yield_reference_range(postpartum_day) or {"p15": 700.0}
    lower = float(ref["p15"])
    margins = [18, 24, 30, 22, 35, 20, 28]
    nursing_ml = _nursing_count_for_day(index) * NURSING_ESTIMATE_ML
    total = int(round(lower - nursing_ml - margins[index % len(margins)]))
    return max(420, total)


def _seed_lactation(conn: sqlite3.Connection) -> None:
    # Pump records are generated from calendar tasks in
    # `_sync_calendar_records_to_plan()`, so startup cannot leave record rows
    # that do not exist in the plan list.
    conn.execute(
        """
        DELETE FROM pumping_log
        WHERE user_id = ?
          AND date(pump_start_time) <= ?
        """,
        (USER_ID, TODAY.date().isoformat()),
    )
    _clear_future_pumping_records(conn)


def _seed_feeding(conn: sqlite3.Connection) -> None:
    """Reset demo feed rows so history can be rebuilt from the seed rules."""

    conn.execute(
        """
        DELETE FROM feeding_log
        WHERE user_id = ?
          AND date(feed_time) <= ?
        """,
        (USER_ID, TODAY.date().isoformat()),
    )
    _clear_future_feeding_records(conn)


def _clear_demo_transient_calendar_items(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        DELETE FROM calendar
        WHERE user_id = ?
          AND plan_id IS NULL
          AND source = '用户输入'
          AND type = '吸奶'
          AND content IN ('追奶观察版：下午强化排乳')
        """,
        (USER_ID,),
    )


def _roll_demo_calendar_to_today(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT MAX(date) AS max_date FROM calendar WHERE user_id = ?",
        (USER_ID,),
    ).fetchone()
    max_date = str(row["max_date"] or "") if row else ""
    if not max_date:
        return
    try:
        current_max = datetime.fromisoformat(max_date).date()
    except ValueError:
        return
    shift_days = (TODAY.date() - current_max).days
    if shift_days == 0:
        return
    modifier = f"{shift_days:+d} days"
    conn.execute(
        """
        UPDATE calendar
        SET date = date(date, ?),
            start_time = CASE
                WHEN start_time IS NULL OR start_time = '' THEN start_time
                ELSE datetime(start_time, ?)
            END,
            end_time = CASE
                WHEN end_time IS NULL OR end_time = '' THEN end_time
                ELSE datetime(end_time, ?)
            END,
            modified_at = CASE
                WHEN modified_at IS NULL OR modified_at = '' THEN modified_at
                ELSE datetime(modified_at, ?)
            END
        WHERE user_id = ?
        """,
        (modifier, modifier, modifier, modifier, USER_ID),
    )


def _clear_future_calendar_items(conn: sqlite3.Connection) -> None:
    today_date = TODAY.date().isoformat()
    conn.execute(
        """
        DELETE FROM calendar
        WHERE user_id = ?
          AND date > ?
        """,
        (USER_ID, today_date),
    )


def _clear_future_pumping_records(conn: sqlite3.Connection) -> None:
    today_date = TODAY.date().isoformat()
    conn.execute(
        """
        DELETE FROM pumping_log
        WHERE user_id = ?
          AND date(pump_start_time) > ?
        """,
        (USER_ID, today_date),
    )


def _clear_future_feeding_records(conn: sqlite3.Connection) -> None:
    today_date = TODAY.date().isoformat()
    conn.execute(
        """
        DELETE FROM feeding_log
        WHERE user_id = ?
          AND date(feed_time) > ?
        """,
        (USER_ID, today_date),
    )


def _sync_calendar_records_to_plan(conn: sqlite3.Connection) -> None:
    """Rebuild pump plans/records and historical nursing rows for the demo."""

    start_date = _calendar_plan_start_date(conn)
    today_date = TODAY.date().isoformat()
    conn.execute(
        """
        DELETE FROM calendar
        WHERE user_id = ?
          AND date >= ?
          AND date <= ?
        """,
        (USER_ID, start_date.isoformat(), today_date),
    )
    conn.execute(
        """
        DELETE FROM pumping_log
        WHERE user_id = ?
          AND date(pump_start_time) <= ?
        """,
        (USER_ID, today_date),
    )
    conn.execute(
        """
        DELETE FROM feeding_log
        WHERE user_id = ?
          AND date(feed_time) <= ?
        """,
        (USER_ID, today_date),
    )

    current_time = _current_demo_time()
    day = start_date
    today = TODAY.date()
    day_index = 0
    while day <= today:
        total = _daily_total_for(datetime.combine(day, datetime.min.time()), day_index)
        amounts = _session_amounts(total)
        for task_index, (time_text, title, _) in enumerate(SESSION_TIMES, start=1):
            start_at = datetime.strptime(f"{day.isoformat()} {time_text}", "%Y-%m-%d %H:%M")
            end_at = start_at + timedelta(minutes=PLAN_PUMP_DURATION_MINUTES)
            finish = "true" if day < today or start_at <= current_time else "false"
            conn.execute(
                """
                INSERT INTO calendar(
                    user_id, plan_id, date, task_id, start_time, end_time,
                    content, type, source, is_milk_pump, finish, created_at, modified_at
                )
                VALUES (?, NULL, ?, ?, ?, ?, ?, '吸奶', '系统生成', 1, ?, ?, ?)
                """,
                (
                    USER_ID,
                    day.isoformat(),
                    task_index,
                    start_at.strftime("%Y-%m-%d %H:%M:%S"),
                    end_at.strftime("%Y-%m-%d %H:%M:%S"),
                    title,
                    finish,
                    start_at.strftime("%Y-%m-%d %H:%M:%S"),
                    current_time.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            if finish == "true":
                conn.execute(
                    """
                    INSERT INTO pumping_log(
                        user_id, pump_start_time, pump_end_time, pump_milk_volum,
                        pump_type, pump_milk_duration, pump_source, pump_title, created_at
                    )
                    VALUES (?, ?, ?, ?, 1, ?, 2, ?, ?)
                    """,
                    (
                        USER_ID,
                        start_at.strftime("%Y-%m-%d %H:%M:%S"),
                        end_at.strftime("%Y-%m-%d %H:%M:%S"),
                        float(amounts[task_index - 1]),
                        PLAN_PUMP_DURATION_MINUTES,
                        title,
                        end_at.strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )
        if day < today:
            _seed_nursing_records(conn, day=day, day_index=day_index)
        day += timedelta(days=1)
        day_index += 1
    _seed_breastfeeding_estimate_anchor(conn, start_date=start_date)


def _calendar_plan_start_date(conn: sqlite3.Connection):
    fallback = TODAY.date() - timedelta(days=PLAN_HISTORY_DAYS)
    row = conn.execute(
        """
        SELECT MIN(date) AS min_date
        FROM calendar
        WHERE user_id = ?
          AND date <= ?
        """,
        (USER_ID, TODAY.date().isoformat()),
    ).fetchone()
    raw = str(row["min_date"] or "") if row else ""
    if not raw:
        return fallback
    try:
        existing = datetime.fromisoformat(raw).date()
    except ValueError:
        return fallback
    return min(existing, fallback)


def _current_demo_time() -> datetime:
    now = datetime.now()
    if now.date() == TODAY.date():
        return now
    return TODAY + timedelta(hours=23, minutes=59, seconds=59)


def _session_amounts(total: int) -> list[int]:
    amounts: list[int] = []
    allocated = 0
    for index, (_, _, ratio) in enumerate(SESSION_TIMES):
        if index == len(SESSION_TIMES) - 1:
            amount = int(total) - allocated
        else:
            amount = int(round(total * ratio))
            allocated += amount
        amounts.append(max(amount, 0))
    return amounts


def _nursing_count_for_day(index: int) -> int:
    return [2, 3, 4, 3, 2, 4, 3][index % 7]


def _seed_nursing_records(conn: sqlite3.Connection, *, day, day_index: int) -> None:
    count = _nursing_count_for_day(day_index)
    for time_text, title in NURSING_TIMES[:count]:
        feed_at = datetime.strptime(f"{day.isoformat()} {time_text}", "%Y-%m-%d %H:%M")
        conn.execute(
            """
            INSERT INTO feeding_log(
                user_id, infant_id, feed_time, feed_milk_volum, feed_type,
                feeding_title, feed_action, created_at
            )
            VALUES (?, ?, ?, ?, '亲喂', ?, 0, ?)
            """,
            (
                USER_ID,
                INFANT_ID,
                feed_at.strftime("%Y-%m-%d %H:%M:%S"),
                18.0,
                title,
                feed_at.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )


def _seed_breastfeeding_estimate_anchor(conn: sqlite3.Connection, *, start_date) -> None:
    anchor_at = datetime.combine(start_date - timedelta(days=1), datetime.min.time()) + timedelta(hours=16, minutes=30)
    conn.execute(
        """
        INSERT INTO feeding_log(
            user_id, infant_id, feed_time, feed_milk_volum, feed_type,
            feeding_title, feed_action, created_at
        )
        VALUES (?, ?, ?, ?, '瓶喂母乳', '亲喂估算参考', 0, ?)
        """,
        (
            USER_ID,
            INFANT_ID,
            anchor_at.strftime("%Y-%m-%d %H:%M:%S"),
            NURSING_ESTIMATE_ML,
            anchor_at.strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )


def main() -> None:
    with _connect() as conn:
        _seed_profiles(conn)
        _seed_growth(conn)
        _seed_lactation(conn)
        _seed_feeding(conn)
        _clear_demo_transient_calendar_items(conn)
        _roll_demo_calendar_to_today(conn)
        _clear_future_calendar_items(conn)
        _sync_calendar_records_to_plan(conn)
        conn.commit()
    print(f"Seeded status demo data for {USER_ID} in {data_store.DB_PATH}")


if __name__ == "__main__":
    main()
