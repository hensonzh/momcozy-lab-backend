from app.infrastructure.db.base import Base
from app.infrastructure.db import models as _models


def test_user_profiles_are_unique_by_user() -> None:
    table = Base.metadata.tables["user_profiles"]
    constraint_names = {constraint.name for constraint in table.constraints}

    assert "user_id" in table.columns
    assert "uq_user_profiles_user_id" in constraint_names


def test_infant_profiles_are_owner_scoped() -> None:
    table = Base.metadata.tables["infant_profiles"]
    index_names = {index.name for index in table.indexes}

    assert "owner_user_id" in table.columns
    assert table.columns["owner_user_id"].nullable is False
    assert "ix_infant_profiles_owner_status" in index_names
