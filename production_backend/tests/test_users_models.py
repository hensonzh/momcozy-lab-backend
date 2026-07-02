from production_backend.app.infrastructure.db.base import Base
from production_backend.app.infrastructure.db import models as _models


def test_user_tables_are_registered_in_metadata() -> None:
    assert "users" in Base.metadata.tables
    assert "auth_identities" in Base.metadata.tables


def test_auth_identity_has_provider_subject_uniqueness() -> None:
    table = Base.metadata.tables["auth_identities"]
    constraint_names = {constraint.name for constraint in table.constraints}

    assert "uq_auth_identities_provider_subject" in constraint_names
