from production_backend.app.infrastructure.db.base import Base
from production_backend.app.infrastructure.db import models as _models


def test_pump_devices_are_unique_per_owner_device() -> None:
    table = Base.metadata.tables["pump_devices"]
    constraint_names = {constraint.name for constraint in table.constraints}

    assert "owner_user_id" in table.columns
    assert "device_id" in table.columns
    assert "uq_pump_devices_owner_device" in constraint_names


def test_pump_telemetry_events_are_indexed_by_owner_device_time() -> None:
    table = Base.metadata.tables["pump_telemetry_events"]
    index_names = {index.name for index in table.indexes}

    assert "payload_json" in table.columns
    assert "ix_pump_telemetry_owner_device_time" in index_names
    assert "ix_pump_telemetry_owner_event_time" in index_names
