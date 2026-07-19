from datetime import date

import pytest

from app.modules.plans.milk_schedule_calendar import (
    MilkScheduleCalendarEventError,
    normalize_milk_schedule_calendar_events,
)


def test_calendar_events_are_normalized_for_preview_and_persistence() -> None:
    events = normalize_milk_schedule_calendar_events(
        [
            {
                "date": "2026-07-14",
                "start_time": "9:05",
                "end_time": "10:35",
                "title": "  团队会议  ",
                "description": "  二楼会议室  ",
            }
        ],
        allowed_dates=[date(2026, 7, 14)],
    )

    assert events == [
        {
            "date": "2026-07-14",
            "start_time": "09:05",
            "end_time": "10:35",
            "title": "团队会议",
            "description": "二楼会议室",
            "duration_minutes": 90,
        }
    ]


@pytest.mark.parametrize(
    ("event", "error_code"),
    [
        (
            {"date": "2026-07-15", "start_time": "09:00", "end_time": "10:00", "title": "会议"},
            "milk_schedule_calendar_event_outside_target_dates",
        ),
        (
            {"date": "2026-07-14", "start_time": "10:00", "end_time": "09:00", "title": "会议"},
            "invalid_milk_schedule_calendar_event_time",
        ),
        (
            {"date": "2026-07-14", "start_time": "09:00", "end_time": "10:00", "title": ""},
            "milk_schedule_calendar_event_title_required",
        ),
    ],
)
def test_calendar_events_reject_ambiguous_or_invalid_values(event: dict, error_code: str) -> None:
    with pytest.raises(MilkScheduleCalendarEventError, match=error_code):
        normalize_milk_schedule_calendar_events([event], allowed_dates=[date(2026, 7, 14)])
