from app.infrastructure.db.base import Base
from app.infrastructure.db import models as _models


def test_diary_entries_are_unique_by_owner_and_date() -> None:
    table = Base.metadata.tables["diary_entries"]
    constraint_names = {constraint.name for constraint in table.constraints}

    assert "owner_user_id" in table.columns
    assert "diary_type" not in table.columns
    assert "entry_date" in table.columns
    assert "uq_diary_entries_owner_date" in constraint_names
