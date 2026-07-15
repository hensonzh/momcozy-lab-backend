from production_backend.app.infrastructure.db.base import Base
from production_backend.app.infrastructure.db import models as _models


def test_plans_are_owner_scoped_and_json_backed() -> None:
    table = Base.metadata.tables["plans"]
    index_names = {index.name for index in table.indexes}

    assert "owner_user_id" in table.columns
    assert "payload_json" in table.columns
    assert "ix_plans_owner_status_updated" in index_names


def test_plan_tasks_are_owner_and_date_scoped() -> None:
    table = Base.metadata.tables["plan_tasks"]
    index_names = {index.name for index in table.indexes}

    assert "owner_user_id" in table.columns
    assert "plan_id" in table.columns
    assert "task_date" in table.columns
    assert "ix_plan_tasks_owner_date_status" in index_names
