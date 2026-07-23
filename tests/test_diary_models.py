from app.infrastructure.db.base import Base
from app.infrastructure.db import models as _models


def test_pregnancy_diary_entries_are_unique_by_owner_date() -> None:
    table = Base.metadata.tables["pregnancy_diary_entries"]
    constraint_names = {constraint.name for constraint in table.constraints}

    assert "owner_user_id" in table.columns
    assert "entry_date" in table.columns
    assert "uq_pregnancy_diary_owner_date" in constraint_names
