from importlib import import_module


MIGRATION = import_module("migrations.versions.20260720_0038_canonicalize_invite_identity_subjects")


def test_invite_identity_cleanup_migration_follows_routing_cleanup() -> None:
    assert MIGRATION.revision == "20260720_0038"
    assert MIGRATION.down_revision == "20260720_0037"


def test_invite_identity_cleanup_migration_canonicalizes_without_deleting_users(monkeypatch) -> None:
    statements: list[str] = []
    monkeypatch.setattr(MIGRATION.op, "execute", statements.append)

    MIGRATION.upgrade()

    sql = statements[0]
    assert "split_part(identity.subject, ':', 1)" in sql
    assert "row_number() OVER" in sql
    assert "UPDATE auth_identities" in sql
    assert "DELETE" not in sql.upper()
