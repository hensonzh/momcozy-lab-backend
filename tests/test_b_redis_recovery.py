"""Redis backup must be B-only, private, and verified by isolated restore."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import b_redis_recovery as recovery


def test_selects_only_one_b_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "a" * 12 + "\n", "")

    monkeypatch.setattr(recovery.subprocess, "run", fake)
    assert recovery.b_redis_id() == "a" * 12
    assert "label=com.docker.compose.project=momcozy-lab-backend-us-east-uat" in calls[0]
    assert "label=com.docker.compose.service=redis" in calls[0]


@pytest.mark.parametrize("output", ["", "a" * 12 + "\n" + "b" * 12 + "\n"])
def test_refuses_missing_or_ambiguous_b_redis(monkeypatch: pytest.MonkeyPatch, output: str) -> None:
    monkeypatch.setattr(recovery.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 0, output, ""))
    with pytest.raises(ValueError, match="B-owned Redis"):
        recovery.b_redis_id()


def test_fails_closed_without_backup_mount_before_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recovery, "validate_root", lambda root: None)
    (tmp_path / "shared").mkdir(mode=0o700)
    (tmp_path / "backups").mkdir(mode=0o700)
    monkeypatch.setattr(recovery, "b_redis_id", lambda: pytest.fail("must not touch Docker"))
    with pytest.raises(ValueError, match="mounted B backup storage"):
        recovery.backup_and_drill(tmp_path)
    assert not list((tmp_path / "backups").iterdir())


def test_refuses_held_release_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recovery, "validate_root", lambda root: None)
    (tmp_path / "shared").mkdir(mode=0o700)
    monkeypatch.setattr(recovery, "b_redis_id", lambda: pytest.fail("must not touch Docker"))
    with patch.object(recovery.fcntl, "flock", side_effect=BlockingIOError):
        with pytest.raises(ValueError, match="lock is held"):
            recovery.backup_and_drill(tmp_path)


def test_snapshot_checks_fresh_bgsave_before_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake(command: list[str]) -> str:
        calls.append(command)
        if command[-1] == "LASTSAVE":
            return "100"
        if command[-1] == "BGSAVE":
            return "Background saving started"
        if command[-2:] == ["INFO", "persistence"]:
            return "rdb_bgsave_in_progress:0\nrdb_last_bgsave_status:ok"
        raise AssertionError(command)

    monkeypatch.setattr(recovery, "_run", fake)
    monkeypatch.setattr(recovery.time, "sleep", lambda seconds: None)
    with pytest.raises(ValueError, match="fresh RDB snapshot"):
        recovery._snapshot("a" * 12, tmp_path / "dump.rdb")
    assert any("BGSAVE" in command for command in calls)
    assert not (tmp_path / "dump.rdb").exists()


def test_backup_writes_manifest_only_after_restored_key_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recovery, "_validate_backup_mount", lambda root: root / "backups")
    monkeypatch.setattr(recovery, "b_redis_id", lambda: "a" * 12)
    monkeypatch.setattr(recovery, "_source_key_count", lambda container: 2)

    def fake_snapshot(container: str, dest: Path) -> None:
        dest.write_bytes(b"synthetic-rdb")
        dest.chmod(0o600)

    monkeypatch.setattr(recovery, "_snapshot", fake_snapshot)
    monkeypatch.setattr(recovery, "_drill", lambda dump, count: count)
    root = tmp_path / "b"
    root.mkdir(mode=0o700)
    (root / "backups").mkdir(mode=0o700)
    run = recovery._backup_locked(root)
    manifest = run / "recovery-verified.json"
    data = json.loads(manifest.read_text())
    assert data["verification"] == "isolated-redis-rdb-restore"
    assert data["key_count"] == 2
    assert manifest.stat().st_mode & 0o777 == 0o600
    assert (run / "dump.rdb").stat().st_mode & 0o777 == 0o600


def test_no_success_manifest_if_restore_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recovery, "_validate_backup_mount", lambda root: root / "backups")
    monkeypatch.setattr(recovery, "b_redis_id", lambda: "a" * 12)
    monkeypatch.setattr(recovery, "_source_key_count", lambda container: 2)
    monkeypatch.setattr(recovery, "_snapshot", lambda container, dest: dest.write_bytes(b"bad"))
    monkeypatch.setattr(recovery, "_drill", lambda dump, count: (_ for _ in ()).throw(ValueError("restore failed")))
    root = tmp_path / "b"
    root.mkdir(mode=0o700)
    (root / "backups").mkdir(mode=0o700)
    with pytest.raises(ValueError, match="restore failed"):
        recovery._backup_locked(root)
    assert not list(root.glob("backups/redis/*/recovery-verified.json"))


def test_drill_uses_no_network_or_persistent_volume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[:2] == ["docker", "exec"]:
            output = "PONG\n" if command[-1] == "ping" else "2\n"
            return subprocess.CompletedProcess(command, 0, output, "")
        return subprocess.CompletedProcess(command, 0, "synthetic-id\n", "")

    monkeypatch.setattr(recovery.subprocess, "run", fake)
    dump = tmp_path / "dump.rdb"
    dump.write_bytes(b"synthetic-rdb")
    assert recovery._drill(dump, 2) == 2
    run = next(command for command in commands if command[:3] == ["docker", "run", "-d"])
    assert "--network" in run and "none" in run
    assert "--tmpfs" in run and "/data:uid=999,gid=1000,mode=0700" in run
    assert all(option not in run for option in ("--publish", "-p", "--volume"))
    assert any(command[:3] == ["docker", "rm", "-f"] for command in commands)
