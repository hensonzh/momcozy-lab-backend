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
    assert {
        "display_name",
        "delivery_date",
        "lactation_advice",
        "feeding_advice",
        "profile_onboarding_skipped_at",
        "profile_onboarding_completed_at",
    }.isdisjoint(table.columns)


def test_baby_profiles_are_owner_scoped() -> None:
    table = Base.metadata.tables["baby_profiles"]
    index_names = {index.name for index in table.indexes}

    assert "owner_user_id" in table.columns
    assert table.columns["owner_user_id"].nullable is False
    assert "name" in table.columns
    assert "sex" in table.columns
    assert table.columns["sex"].nullable is False
    assert table.columns["feeding_mode"].nullable is False
    assert table.columns["version"].nullable is False
    assert {"infant_name", "sex_at_birth", "status", "birth_weight_kg", "gestational_age_at_birth_days"}.isdisjoint(table.columns)
    assert "ix_baby_profiles_owner_deleted_at" in index_names


def test_maternal_profiles_store_general_current_delivery_summary() -> None:
    table = Base.metadata.tables["maternal_profiles"]
    constraint_names = {constraint.name for constraint in table.constraints}

    assert {
        "owner_user_id",
        "delivery_count",
        "latest_delivery_method",
        "latest_delivery_date",
        "has_cesarean_history",
    } <= set(table.columns.keys())
    assert "current_feeding_mode" not in table.columns
    assert "uq_maternal_profiles_owner_user_id" in constraint_names
    assert "ck_maternal_profiles_first_delivery_prior_cesarean" in constraint_names
    assert {
        "pregnancy_id",
        "delivery_history",
        "previous_delivery_method",
        "postpartum_days",
        "infant_age_days",
        "infant_age_months",
    }.isdisjoint(table.columns)


def test_lactation_profiles_store_only_lactation_specific_state() -> None:
    table = Base.metadata.tables["lactation_profiles"]
    constraint_names = {constraint.name for constraint in table.constraints}

    assert {"owner_user_id", "current_feeding_mode"} <= set(table.columns.keys())
    assert {
        "delivery_count",
        "latest_delivery_method",
        "latest_delivery_date",
        "has_cesarean_history",
    }.isdisjoint(table.columns)
    assert "uq_lactation_profiles_owner_user_id" in constraint_names


def test_maternal_current_delivery_infants_support_multiple_babies() -> None:
    maternal_table = Base.metadata.tables["maternal_profiles"]
    link_table = Base.metadata.tables["maternal_current_delivery_infants"]
    constraint_names = {constraint.name for constraint in link_table.constraints}

    assert "current_infant_id" not in maternal_table.columns
    assert {
        "maternal_profile_id",
        "infant_id",
        "birth_order",
    } <= set(link_table.columns.keys())
    assert "uq_maternal_current_delivery_infants_birth_order" in constraint_names
    assert "uq_maternal_current_delivery_infants_infant_id" in constraint_names
    assert "maternal_lactation_profiles" not in Base.metadata.tables
    assert "maternal_lactation_profile_infants" not in Base.metadata.tables
