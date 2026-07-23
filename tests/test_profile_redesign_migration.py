from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "versions" / "20260722_0042_redesign_profiles.py"


def test_profile_redesign_migration_replaces_legacy_contract() -> None:
    source = MIGRATION.read_text()

    assert 'revision = "20260722_0042"' in source
    assert 'down_revision = "20260721_0041"' in source
    for column_name in (
        "lactation_advice",
        "feeding_advice",
        "profile_onboarding_skipped_at",
        "profile_onboarding_completed_at",
    ):
        assert f'op.drop_column("user_profiles", "{column_name}")' in source
    assert 'op.drop_column("infant_profiles", "status")' in source
    assert 'op.alter_column("user_profiles", "display_name", new_column_name="preferred_name")' in source
    assert 'op.alter_column("user_profiles", "delivery_date", new_column_name="estimated_due_date")' in source
    assert 'op.alter_column("infant_profiles", "infant_name", new_column_name="name")' in source
    assert 'op.alter_column("infant_profiles", "sex", new_column_name="sex_at_birth")' in source
    assert 'op.drop_column("users", "display_name")' in source
    assert "INSERT INTO user_profiles" in source
    assert "ON CONFLICT (user_id) DO NOTHING" in source
    assert "p.preferred_name IS NULL OR btrim(p.preferred_name) = ''" in source
    assert "AND btrim(u.display_name) <> ''" in source
    assert "btrim(u.display_name) <> 'Momcozy 体验用户'" in source
    assert 'op.add_column(\n        "users",\n        sa.Column("display_name"' in source
    assert "SET display_name = COALESCE(p.preferred_name, '')" in source
    for canonical_value in ("female", "male", "intersex", "unknown", "undisclosed"):
        assert f"'{canonical_value}'" in source
    for legacy_alias in ("女", "男", "f", "m"):
        assert f"'{legacy_alias}'" in source
    assert "ELSE 'unknown'" in source
    assert 'op.create_index("ix_user_profiles_estimated_due_date"' in source
    assert 'op.create_index("ix_infant_profiles_owner_deleted_at"' in source
