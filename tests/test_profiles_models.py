from app.infrastructure.db.base import Base
from app.infrastructure.db import models as _models


def test_users_do_not_duplicate_profile_name() -> None:
    table = Base.metadata.tables["users"]

    assert "display_name" not in table.columns


def test_user_profiles_are_unique_by_user() -> None:
    table = Base.metadata.tables["user_profiles"]
    constraint_names = {constraint.name for constraint in table.constraints}

    assert "user_id" in table.columns
    assert "uq_user_profiles_user_id" in constraint_names
    assert "preferred_name" in table.columns
    assert table.columns["preferred_name"].nullable is True
    assert "estimated_due_date" in table.columns
    assert "ix_user_profiles_estimated_due_date" in {
        index.name for index in table.indexes
    }
    assert {
        "display_name",
        "delivery_date",
        "lactation_advice",
        "feeding_advice",
        "profile_onboarding_skipped_at",
        "profile_onboarding_completed_at",
    }.isdisjoint(table.columns)


def test_infant_profiles_are_owner_scoped() -> None:
    table = Base.metadata.tables["infant_profiles"]
    index_names = {index.name for index in table.indexes}

    assert "owner_user_id" in table.columns
    assert table.columns["owner_user_id"].nullable is False
    assert "name" in table.columns
    assert "sex_at_birth" in table.columns
    assert table.columns["sex_at_birth"].nullable is True
    assert {"infant_name", "sex", "status"}.isdisjoint(table.columns)
    assert "ix_infant_profiles_owner_deleted_at" in index_names
