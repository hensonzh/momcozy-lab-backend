import importlib


def test_lactation_context_migration_extends_current_head() -> None:
    migration = importlib.import_module("migrations.versions.20260723_0043_add_lactation_analysis_context")

    assert migration.revision == "20260723_0043"
    assert migration.down_revision == "20260722_0042"


def test_multi_infant_lactation_context_migration_extends_lactation_profile() -> None:
    migration = importlib.import_module("migrations.versions.20260723_0044_expand_lactation_context_to_multiple_infants")

    assert migration.revision == "20260723_0044"
    assert migration.down_revision == "20260723_0043"


def test_profile_boundary_migration_extends_multi_infant_context() -> None:
    migration = importlib.import_module("migrations.versions.20260723_0045_split_general_and_lactation_profiles")

    assert migration.revision == "20260723_0045"
    assert migration.down_revision == "20260723_0044"
