from app.infrastructure.db.base import Base
from app.infrastructure.db import models as _models


def test_feeding_records_are_owner_and_infant_scoped() -> None:
    table = Base.metadata.tables["feeding_records"]
    index_names = {index.name for index in table.indexes}

    assert "owner_user_id" in table.columns
    assert "infant_id" in table.columns
    assert "ix_feeding_records_owner_infant_time" in index_names


def test_pumping_records_are_owner_scoped() -> None:
    table = Base.metadata.tables["pumping_records"]
    index_names = {index.name for index in table.indexes}

    assert "owner_user_id" in table.columns
    assert "ix_pumping_records_owner_start" in index_names


def test_growth_records_are_owner_and_infant_scoped() -> None:
    table = Base.metadata.tables["growth_records"]
    index_names = {index.name for index in table.indexes}

    assert "owner_user_id" in table.columns
    assert "infant_id" in table.columns
    assert "ix_growth_records_owner_infant_measured" in index_names
