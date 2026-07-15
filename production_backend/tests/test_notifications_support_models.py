from production_backend.app.infrastructure.db.base import Base
from production_backend.app.infrastructure.db import models as _models


def test_notifications_are_owner_scoped_and_payload_backed() -> None:
    table = Base.metadata.tables["notifications"]
    index_names = {index.name for index in table.indexes}

    assert "owner_user_id" in table.columns
    assert "payload_json" in table.columns
    assert "ix_notifications_owner_status_created" in index_names
    assert "ix_notifications_owner_type_created" in index_names


def test_support_tickets_are_owner_scoped_and_uniquely_numbered() -> None:
    table = Base.metadata.tables["support_tickets"]
    index_names = {index.name for index in table.indexes}
    constraint_names = {constraint.name for constraint in table.constraints}

    assert "owner_user_id" in table.columns
    assert "ticket_number" in table.columns
    assert "payload_json" in table.columns
    assert "uq_support_tickets_ticket_number" in constraint_names
    assert "ix_support_tickets_owner_status_updated" in index_names
