from datetime import date, datetime, timezone
from uuid import uuid4

from production_backend.app.modules.records import domain
from production_backend.app.modules.records.models import PumpingRecord


def test_records_domain_validates_required_measurements() -> None:
    assert domain.has_feeding_measurement(volume_ml=90, duration_seconds=None) is True
    assert domain.has_feeding_measurement(volume_ml=None, duration_seconds=300) is True
    assert domain.has_feeding_measurement(volume_ml=None, duration_seconds=None) is False

    assert domain.has_pumping_measurement(milk_volume_ml=120, duration_seconds=None) is True
    assert domain.has_pumping_measurement(milk_volume_ml=None, duration_seconds=600) is True
    assert domain.has_pumping_measurement(milk_volume_ml=None, duration_seconds=None) is False

    assert domain.has_growth_measurement(height_cm=None, weight_kg=4.2, head_cm=None) is True
    assert domain.has_growth_measurement(height_cm=None, weight_kg=None, head_cm=None) is False


def test_records_domain_validates_limits_and_growth_update_fields() -> None:
    assert domain.is_valid_list_limit(1) is True
    assert domain.is_valid_list_limit(100) is True
    assert domain.is_valid_list_limit(0) is False
    assert domain.is_valid_list_limit(101) is False

    assert domain.is_valid_trend_days(1) is True
    assert domain.is_valid_trend_days(90) is True
    assert domain.is_valid_trend_days(0) is False
    assert domain.is_valid_trend_days(91) is False

    assert domain.unsupported_growth_update_fields({"weight_kg": 4.8}) == set()
    assert domain.unsupported_growth_update_fields({"weight_kg": 4.8, "status": "deleted"}) == {"status"}


def test_records_domain_computes_growth_measurements_after_update() -> None:
    assert domain.growth_measurements_after_update(
        current_height_cm=52.0,
        current_weight_kg=4.2,
        current_head_cm=None,
        updates={"weight_kg": 4.8},
    ) == (52.0, 4.8, None)

    assert domain.growth_measurements_after_update(
        current_height_cm=None,
        current_weight_kg=4.2,
        current_head_cm=None,
        updates={"weight_kg": None},
    ) == (None, None, None)


def test_records_domain_builds_measured_milk_trends_from_timezone_aware_and_naive_pumpings() -> None:
    first = _pumping(start_time=datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc), volume=90)
    second = _pumping(start_time=datetime(2026, 7, 1, 18, 0), volume=35.555)
    outside = _pumping(start_time=datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc), volume=50)

    trends = domain.build_measured_milk_trend_days(
        pumpings=[first, second, outside],
        first_day=date(2026, 7, 1),
        days=2,
    )

    assert trends[0].date == date(2026, 7, 1)
    assert trends[0].pumped_milk_volume_ml == 125.56
    assert trends[0].pumping_count == 2
    assert trends[1].date == date(2026, 7, 2)
    assert trends[1].pumped_milk_volume_ml == 0
    assert trends[1].pumping_count == 0


def test_records_domain_builds_default_trend_dates_and_query_window() -> None:
    now = datetime(2026, 7, 5, 10, 0, tzinfo=timezone.utc)

    assert domain.default_trend_start_date(now=now, days=3, include_today=True) == date(2026, 7, 3)
    assert domain.default_trend_start_date(now=now, days=3, include_today=False) == date(2026, 7, 2)

    start_at, end_at = domain.trend_datetime_window(first_day=date(2026, 7, 3), days=3)
    assert start_at == datetime(2026, 7, 3, 0, 0, tzinfo=timezone.utc)
    assert end_at == datetime(2026, 7, 6, 0, 0, tzinfo=timezone.utc)


def _pumping(*, start_time: datetime, volume: float | None) -> PumpingRecord:
    return PumpingRecord(
        id=uuid4(),
        owner_user_id=uuid4(),
        pump_start_time=start_time,
        pump_end_time=None,
        milk_volume_ml=volume,
        pump_type="",
        duration_seconds=None,
        source="manual",
        title="",
        status="active",
    )
