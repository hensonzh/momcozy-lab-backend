from types import SimpleNamespace

from migrations.retained_schema import RETAINED_COLUMNS, RETAINED_TABLES, include_object


def test_retired_objects_are_explicit_and_new_drift_is_still_detected() -> None:
    assert "care_service_events" in RETAINED_TABLES
    assert ("device_sessions", "mfa_verified_at") in RETAINED_COLUMNS
    assert not include_object(None, "care_service_events", "table", True, None)
    column = SimpleNamespace(table=SimpleNamespace(name="device_sessions"))
    assert not include_object(column, "mfa_verified_at", "column", True, None)
    assert include_object(None, "unexpected_table", "table", True, None)
    assert include_object(column, "unexpected_column", "column", True, None)
    assert include_object(None, "care_service_events", "table", False, None)
    assert include_object(None, "care_service_events", "table", True, object())
