from app.infrastructure.db.base import Base
from app.infrastructure.db import models as _models


def test_files_table_has_owner_scope() -> None:
    table = Base.metadata.tables["files"]

    assert "owner_user_id" in table.columns
    assert table.columns["owner_user_id"].nullable is False


def test_files_table_indexes_owner_scope_and_object_key() -> None:
    table = Base.metadata.tables["files"]
    index_names = {index.name for index in table.indexes}

    assert "ix_files_owner_status_created" in index_names
    assert "ix_files_object_key" in index_names
