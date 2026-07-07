from production_backend.app.infrastructure.db.base import Base
from production_backend.app.infrastructure.db import models as _models


def test_invite_code_table_is_registered_in_metadata() -> None:
    assert "invite_codes" in Base.metadata.tables


def test_invite_code_table_has_admin_and_binding_columns() -> None:
    table = Base.metadata.tables["invite_codes"]

    for column_name in [
        "code",
        "status",
        "label",
        "assigned_to",
        "bound_device_id",
        "bound_user_id",
        "created_by_service",
        "disabled_at",
    ]:
        assert column_name in table.columns

    assert "ix_invite_codes_code" in {index.name for index in table.indexes}
