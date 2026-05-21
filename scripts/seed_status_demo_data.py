#!/usr/bin/env python3
"""Seed status overview demo data for lactation and baby growth charts."""

from __future__ import annotations

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
DELIVERY_DATE = "2026-03-01"
TODAY = datetime(2026, 5, 21)


# Weight curve starts inside the WHO girls P25-P50 band, then visibly
# decelerates over the last ~6 weeks, drifting down to the P10 line.
# A percentile drop like this is a clear clinical red flag in pediatrics
# (crossing two major centiles downward) and turns the chart into a
# textbook "milk supply -> baby intake -> weight stall" story that
# justifies a lactation-boost (追奶) plan.
GROWTH_POINTS = [
    ("2026-03-01 09:20:00", 3.00, 49.0, 34.0),  # birth, ~P25
    ("2026-03-15 09:25:00", 3.55, 52.0, 35.5),  # 2 wk, ~P25-P50 (39 g/d)
    ("2026-03-29 09:30:00", 4.05, 54.5, 36.6),  # 4 wk, ~P25-P50 (36 g/d)
    ("2026-04-12 09:40:00", 4.40, 56.0, 37.3),  # 6 wk, slipping toward P25 (25 g/d)
    ("2026-04-26 09:50:00", 4.55, 57.0, 37.7),  # 8 wk, ~P15-P25 (11 g/d, slow)
    ("2026-05-10 09:50:00", 4.70, 57.8, 38.0),  # 10 wk, ~P10-P15 (11 g/d, persistent slow)
    ("2026-05-17 09:40:00", 4.78, 58.1, 38.1),  # 11 wk, ~P10 line (11 g/d)
    ("2026-05-21 09:21:00", 4.85, 58.3, 38.2),  # 11.4 wk, on P10 (17 g/d, still under-grown)
]


SESSION_TIMES = [
    ("06:20", "晨间吸乳", 0.24),
    ("10:10", "上午吸乳", 0.20),
    ("14:20", "午后吸乳", 0.19),
    ("18:30", "傍晚吸乳", 0.18),
    ("22:20", "睡前吸乳", 0.19),
]


# Times for direct breastfeeding (亲喂) and one bottle-feed of pumped milk per
# day. They sit between pumping slots so the day reads as a realistic combo
# feeding schedule (pump + nurse + occasional bottle relay).
BREASTFEEDING_TIMES = [
    ("03:00", "夜间亲喂"),
    ("08:00", "早起亲喂"),
    ("12:30", "午前亲喂"),
    ("20:30", "睡前亲喂"),
]
# Bottle volume seeds `estimate_breastfeeding_milk()`, which then becomes
# the per-session breastfeeding estimate. Drop to ~30ml so the combined
# estimate also falls clearly below the reference band — modeling a baby
# who is taking little per session because supply isn't keeping up
# (low transfer / quick give-up at breast). This is the upstream cause of
# the weight slowdown shown in GROWTH_POINTS.
BOTTLE_FEED = ("16:30", "下午瓶喂母乳", 30.0)


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
            "近 30 天吸乳总量持续低于目标参考区间，平均约为 p15 的 70-75%；含亲喂估算也明显在下沿以下，提示母乳产出与宝宝摄入都偏少。需要尽快制定追奶计划，并结合医生/儿科建议评估是否需要短期补充喂养。",
            "宝宝近 4-6 周体重增长明显放缓，已从 P25-P50 区间逐步下沉到 P10 附近，伴随每日吸乳量持续低于参考区间，需要重点关注摄入和体重曲线。建议尽快与儿科沟通并启动温和追奶方案。",
            "母乳产出与含亲喂估算都低于目标参考区间，宝宝体重曲线从 P25-P50 滑落到 P10 附近、连续多周增长放缓。叙事一致：是供给不足导致摄入不够，适合直接生成追奶计划并提示就医评估。",
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
    # Yield sits ~150-240ml below p15 — clearly insufficient supply that
    # matches the baby's slowing weight curve. 30-day average around
    # 70-75% of p15, never crossing back into the band. Weekend bonus
    # remains to keep a natural rest-day uptick.
    postpartum_day = (day.date() - datetime.fromisoformat(DELIVERY_DATE).date()).days + 1
    ref = get_yield_reference_range(postpartum_day) or {"p15": 700.0}
    lower = float(ref["p15"])
    offsets = [
        180, 165, 200, 155, 225, 170, 145,
        195, 175, 150, 215, 160, 140, 185,
        210, 155, 175, 145, 220, 165, 138,
        190, 170, 150, 205, 165, 142, 180,
        215, 158,
    ]
    weekend_bonus = 10 if day.weekday() >= 5 else 0
    total = int(round(lower - offsets[index % len(offsets)] + weekend_bonus))
    return max(440, min(total, int(lower) - 100))


def _seed_lactation(conn: sqlite3.Connection) -> None:
    start = TODAY - timedelta(days=29)
    end = TODAY + timedelta(days=1)
    conn.execute(
        """
        DELETE FROM pumping_log
        WHERE user_id = ?
          AND pump_start_time >= ?
          AND pump_start_time < ?
        """,
        (
            USER_ID,
            start.strftime("%Y-%m-%d 00:00:00"),
            end.strftime("%Y-%m-%d 00:00:00"),
        ),
    )

    for index in range(30):
        day = start + timedelta(days=index)
        total = _daily_total_for(day, index)
        allocated = 0
        for session_index, (time_text, title, ratio) in enumerate(SESSION_TIMES):
            if session_index == len(SESSION_TIMES) - 1:
                amount = total - allocated
            else:
                amount = int(round(total * ratio))
                allocated += amount
            start_at = datetime.strptime(f"{day:%Y-%m-%d} {time_text}", "%Y-%m-%d %H:%M")
            end_at = start_at + timedelta(minutes=20)
            conn.execute(
                """
                INSERT INTO pumping_log(
                    user_id, pump_start_time, pump_end_time, pump_milk_volum,
                    pump_type, pump_milk_duration, pump_source, pump_title, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    USER_ID,
                    start_at.strftime("%Y-%m-%d %H:%M:%S"),
                    end_at.strftime("%Y-%m-%d %H:%M:%S"),
                    float(amount),
                    1,
                    20,
                    1,
                    title,
                    end_at.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )


def _seed_feeding(conn: sqlite3.Connection) -> None:
    """Seed direct breastfeeding + occasional bottle feed so the chart's
    含亲喂估算 (`total_milk_estimate`) line has real input to render."""

    start = TODAY - timedelta(days=29)
    end = TODAY + timedelta(days=1)
    conn.execute(
        """
        DELETE FROM feeding_log
        WHERE user_id = ?
          AND feed_time >= ?
          AND feed_time < ?
        """,
        (
            USER_ID,
            start.strftime("%Y-%m-%d 00:00:00"),
            end.strftime("%Y-%m-%d 00:00:00"),
        ),
    )

    bottle_time_text, bottle_title, bottle_ml = BOTTLE_FEED
    for index in range(30):
        day = start + timedelta(days=index)
        for time_text, title in BREASTFEEDING_TIMES:
            feed_at = datetime.strptime(f"{day:%Y-%m-%d} {time_text}", "%Y-%m-%d %H:%M")
            conn.execute(
                """
                INSERT INTO feeding_log(
                    user_id, infant_id, feed_time, feed_milk_volum, feed_type,
                    feeding_title, feed_action, created_at
                )
                VALUES (?, ?, ?, NULL, ?, ?, 0, ?)
                """,
                (
                    USER_ID,
                    INFANT_ID,
                    feed_at.strftime("%Y-%m-%d %H:%M:%S"),
                    "亲喂",
                    title,
                    feed_at.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )

        bottle_at = datetime.strptime(f"{day:%Y-%m-%d} {bottle_time_text}", "%Y-%m-%d %H:%M")
        conn.execute(
            """
            INSERT INTO feeding_log(
                user_id, infant_id, feed_time, feed_milk_volum, feed_type,
                feeding_title, feed_action, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                USER_ID,
                INFANT_ID,
                bottle_at.strftime("%Y-%m-%d %H:%M:%S"),
                float(bottle_ml),
                "瓶喂母乳",
                bottle_title,
                bottle_at.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )


def main() -> None:
    with _connect() as conn:
        _seed_profiles(conn)
        _seed_growth(conn)
        _seed_lactation(conn)
        _seed_feeding(conn)
        conn.commit()
    print(f"Seeded status demo data for {USER_ID} in {data_store.DB_PATH}")


if __name__ == "__main__":
    main()
