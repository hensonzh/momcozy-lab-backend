from production_backend.app.infrastructure.db.base import Base
from production_backend.app.infrastructure.db import models as _models


def test_pregnancy_diary_entries_are_unique_by_owner_date() -> None:
    table = Base.metadata.tables["pregnancy_diary_entries"]
    constraint_names = {constraint.name for constraint in table.constraints}

    assert "owner_user_id" in table.columns
    assert "entry_date" in table.columns
    assert "uq_pregnancy_diary_owner_date" in constraint_names


def test_pregnancy_diary_has_no_health_consultation_coupling_table() -> None:
    assert "pregnancy_diary_health_notes" not in Base.metadata.tables


def test_pregnancy_diary_auto_capture_settings_are_owner_scoped_and_opt_in() -> None:
    table = Base.metadata.tables["pregnancy_diary_settings"]

    assert list(table.primary_key.columns.keys()) == ["owner_user_id"]
    assert table.columns["auto_capture_enabled"].nullable is False
    assert str(table.columns["auto_capture_enabled"].server_default.arg) == "false"
