from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "versions" / "20260721_0041_drop_outbox_jobs.py"


def test_outbox_removal_migration_drops_retired_table() -> None:
    source = MIGRATION.read_text()

    assert 'revision = "20260721_0041"' in source
    assert 'down_revision = "20260721_0040"' in source
    assert 'op.drop_table("outbox_jobs")' in source
