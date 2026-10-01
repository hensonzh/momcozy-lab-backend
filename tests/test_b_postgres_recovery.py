"""B PostgreSQL recovery checks must not use A containers or a public port."""

from pathlib import Path
import subprocess

import pytest

from scripts import b_postgres_recovery as recovery


def test_selects_only_one_b_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    def fake(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "a" * 12 + "\n", "")
    monkeypatch.setattr(recovery.subprocess, "run", fake)
    assert recovery.b_postgres_id() == "a" * 12
    assert "label=com.docker.compose.project=momcozy-lab-backend-us-east-uat" in calls[0]
    assert "label=com.docker.compose.service=postgres" in calls[0]
    assert "--format" in calls[0]


def test_rejects_missing_or_ambiguous_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    for output in ("", "a" * 12 + "\n" + "b" * 12 + "\n"):
        monkeypatch.setattr(recovery.subprocess, "run", lambda command, output=output, **kwargs: subprocess.CompletedProcess(command, 0, output, ""))
        with pytest.raises(ValueError):
            recovery.b_postgres_id()


def test_backup_refuses_non_b_root_or_symlink(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        recovery.validate_root(tmp_path)
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(ValueError):
        recovery.validate_root(link)


def test_backup_fails_closed_without_b_postgres(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recovery, "validate_root", lambda root: None)
    (tmp_path / "shared").mkdir(mode=0o700)
    monkeypatch.setattr(recovery, "_validate_backup_mount", lambda root: root / "backups")
    monkeypatch.setattr(recovery, "b_postgres_id", lambda: (_ for _ in ()).throw(ValueError("missing B PG")))
    with pytest.raises(ValueError, match="missing B PG"):
        recovery.backup_and_drill(tmp_path)
    assert not (tmp_path / "backups").exists()




def test_backup_refuses_missing_data_mount_before_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recovery, "validate_root", lambda root: None)
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o700)
    backups = tmp_path / "backups"
    backups.mkdir(mode=0o700)
    monkeypatch.setattr(recovery, "b_postgres_id", lambda: pytest.fail("Docker must not run"))
    with pytest.raises(ValueError, match="mounted B backup storage"):
        recovery.backup_and_drill(tmp_path)
    assert not list(backups.iterdir())


def test_backup_refuses_backups_on_system_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recovery, "validate_root", lambda root: None)
    (tmp_path / "shared").mkdir(mode=0o700)
    backup = tmp_path / "backups"
    backup.mkdir(mode=0o700)
    monkeypatch.setattr(recovery.os.path, "ismount", lambda path: path == backup)
    monkeypatch.setattr(recovery, "b_postgres_id", lambda: pytest.fail("Docker must not run"))
    with pytest.raises(ValueError, match="/data"):
        recovery.backup_and_drill(tmp_path)

def test_backup_refuses_bind_mount_from_wrong_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recovery, "validate_root", lambda root: None)
    (tmp_path / "shared").mkdir(mode=0o700)
    backup = tmp_path / "backups"
    backup.mkdir(mode=0o700)
    source = tmp_path / "another-backup-source"
    source.mkdir(mode=0o700)
    monkeypatch.setattr(recovery, "BACKUP_SOURCE", source)
    monkeypatch.setattr(recovery.os.path, "ismount", lambda path: path in (backup, Path("/data")))
    monkeypatch.setattr(recovery.os.path, "samefile", lambda path, other: False)
    monkeypatch.setattr(recovery, "b_postgres_id", lambda: pytest.fail("Docker must not run"))
    with pytest.raises(ValueError, match="/data"):
        recovery.backup_and_drill(tmp_path)
    assert not list(backup.iterdir())


def test_drill_requires_restored_schema_revision(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:2] == ["docker", "exec"] and "pg_isready" in command:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:2] == ["docker", "exec"] and "psql" in command:
            return subprocess.CompletedProcess(command, 0, "wrong_revision\n", "")
        return subprocess.CompletedProcess(command, 0, "synthetic-container\n", "")
    monkeypatch.setattr(recovery.subprocess, "run", fake_run)
    dump = tmp_path / "product.dump"
    dump.write_bytes(b"synthetic")
    with pytest.raises(ValueError, match="revision"):
        recovery._drill("momcozy_lab_backend_uat", dump, "expected_revision")
    assert any(command[:3] == ["docker", "rm", "-f"] for command in calls)
    assert any(command[:3] == ["docker", "run", "-d"] and "none" in command for command in calls)


def test_drill_checks_real_restored_revision_not_only_select_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "psql" in command:
            return subprocess.CompletedProcess(command, 0, "expected_revision\n", "")
        return subprocess.CompletedProcess(command, 0, "synthetic-container\n", "")
    monkeypatch.setattr(recovery.subprocess, "run", fake_run)
    dump = tmp_path / "product.dump"
    dump.write_bytes(b"synthetic")
    recovery._drill("momcozy_lab_backend_uat", dump, "expected_revision")


def test_drill_validates_dump_before_starting_scratch_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dump = tmp_path / "missing.dump"
    monkeypatch.setattr(recovery.subprocess, "run", lambda *args, **kwargs: pytest.fail("Docker must not run"))
    with pytest.raises(ValueError, match="dump"):
        recovery._drill("momcozy_lab_backend_uat", dump, "abc123")


def test_backup_refuses_held_b_lock_without_starting_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import patch
    monkeypatch.setattr(recovery, "validate_root", lambda root: None)
    (tmp_path / "shared").mkdir(mode=0o700)
    with patch.object(recovery.fcntl, "flock", side_effect=BlockingIOError):
        with pytest.raises(ValueError, match="lock is held"):
            recovery.backup_and_drill(tmp_path)
    assert not (tmp_path / "backups").exists()


def test_backup_rejects_symlink_lock_before_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recovery, "validate_root", lambda root: None)
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o700)
    link = shared / "north-america-staging-release.lock"
    link.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="unsafe"):
        recovery.backup_and_drill(tmp_path)


def test_dump_rejects_partial_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(command: list[str], **kwargs: object) -> None:
        output = kwargs.get("stdout")
        assert output is not None
        output.write(b"partial")  # type: ignore[attr-defined]
        raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(recovery.subprocess, "run", broken)
    destination = tmp_path / "broken.dump"
    with pytest.raises(subprocess.CalledProcessError):
        recovery._dump("a" * 12, "momcozy_lab_backend_uat", destination)
    assert not destination.exists()


def test_backup_writes_private_manifest_only_after_both_restores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recovery, "_validate_backup_mount", lambda root: root / "backups")
    monkeypatch.setattr(recovery, "b_postgres_id", lambda: "a" * 12)
    def revision(command: list[str]) -> str:
        return "synthetic_rev"
    def fake_dump(container: str, database: str, destination: Path) -> None:
        destination.write_bytes(database.encode())
        destination.chmod(0o600)
    verified: list[str] = []
    def drill(database: str, dump: Path, expected_revision: str) -> None:
        verified.append(database)
        assert expected_revision == "synthetic_rev"
    monkeypatch.setattr(recovery, "_run", revision)
    monkeypatch.setattr(recovery, "_dump", fake_dump)
    monkeypatch.setattr(recovery, "_drill", drill)
    root = tmp_path / "b"
    root.mkdir(mode=0o700)
    (root / "backups").mkdir(mode=0o700)
    run = recovery._backup_locked(root)
    assert verified == list(recovery.DATABASES)
    import json
    data = json.loads((run / "recovery-verified.json").read_text())
    assert set(data["databases"]) == set(recovery.DATABASES)
    assert (run / "recovery-verified.json").stat().st_mode & 0o777 == 0o600


def test_backup_does_not_write_success_manifest_if_restore_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recovery, "_validate_backup_mount", lambda root: root / "backups")
    monkeypatch.setattr(recovery, "b_postgres_id", lambda: "a" * 12)
    monkeypatch.setattr(recovery, "_run", lambda command: "synthetic_rev")
    def fake_dump(container: str, database: str, destination: Path) -> None:
        destination.write_bytes(b"synthetic")
    monkeypatch.setattr(recovery, "_dump", fake_dump)
    def fail(database: str, dump: Path, revision: str) -> None:
        raise ValueError("failed isolated restore")
    monkeypatch.setattr(recovery, "_drill", fail)
    root = tmp_path / "b"
    root.mkdir(mode=0o700)
    (root / "backups").mkdir(mode=0o700)
    with pytest.raises(ValueError, match="failed isolated restore"):
        recovery._backup_locked(root)
    assert list(root.glob("backups/postgres/*/recovery-verified.json")) == []


def test_backup_uses_a_clean_database_baseline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(recovery, "_validate_backup_mount", lambda root: root / "backups")
    monkeypatch.setattr(recovery, "b_postgres_id", lambda: "a" * 12)
    def fake_dump(container: str, database: str, destination: Path) -> None:
        destination.write_bytes(database.encode())
        destination.chmod(0o600)
    monkeypatch.setattr(recovery, "_dump", fake_dump)
    changed = False
    def source_query(command: list[str]) -> str:
        return "new_revision" if changed else "synthetic_rev"
    monkeypatch.setattr(recovery, "_run", source_query)
    def mutate(database: str, dump: Path, revision: str) -> None:
        # Simulate migration of the live source during a long backup window.
        nonlocal changed
        changed = True
    monkeypatch.setattr(recovery, "_drill", mutate)
    root = tmp_path / "b"
    root.mkdir(mode=0o700)
    (root / "backups").mkdir(mode=0o700)
    with pytest.raises(ValueError, match="changed"):
        recovery._backup_locked(root)
    assert list(root.glob("backups/postgres/*/recovery-verified.json")) == []
