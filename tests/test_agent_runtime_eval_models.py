from app.infrastructure.db.base import Base
from app.infrastructure.db import models as _models


def test_agent_eval_cases_are_suite_and_status_scoped() -> None:
    table = Base.metadata.tables["agent_eval_cases"]
    index_names = {index.name for index in table.indexes}

    assert "suite" in table.columns
    assert "input_json" in table.columns
    assert "expected_behavior_json" in table.columns
    assert "expected_tool_calls_json" in table.columns
    assert "source_run_id" in table.columns
    assert "ix_agent_eval_cases_suite_status" in index_names


def test_agent_memory_settings_are_owner_scoped() -> None:
    table = Base.metadata.tables["agent_memory_settings"]
    index_names = {index.name for index in table.indexes}

    assert list(table.primary_key.columns.keys()) == ["owner_user_id"]
    assert "memory_enabled" in table.columns
    assert "updated_at" in table.columns
    assert "ix_agent_memory_settings_owner_updated" in index_names
