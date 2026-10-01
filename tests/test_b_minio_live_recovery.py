"""Live B MinIO recovery refuses the wrong state and never targets A."""

import subprocess
from pathlib import Path

import pytest

from scripts import b_minio_recovery as recovery


def test_selects_exactly_one_b_minio(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    def fake(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, ("a" * 12 + "\n").encode(), b"")
    monkeypatch.setattr(recovery.subprocess, "run", fake)
    assert recovery.b_minio_id() == "a" * 12
    assert "label=com.docker.compose.project=momcozy-lab-backend-us-east-uat" in commands[0]
    assert "label=com.docker.compose.service=minio" in commands[0]


def test_missing_or_ambiguous_minio_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    for output in ("", "a" * 12 + "\n" + "b" * 12 + "\n"):
        monkeypatch.setattr(recovery.subprocess, "run", lambda command, output=output, **kwargs: subprocess.CompletedProcess(command, 0, output.encode(), b""))
        with pytest.raises(ValueError, match="exactly one"):
            recovery.b_minio_id()


def test_no_mount_means_no_minio_access(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recovery, "validate_root", lambda root: None)
    (tmp_path / "shared").mkdir(mode=0o700)
    (tmp_path / "backups").mkdir(mode=0o700)
    monkeypatch.setattr(recovery, "b_minio_id", lambda: pytest.fail("must not touch Docker"))
    with pytest.raises(ValueError, match="mounted B backup storage"):
        recovery.backup_and_drill(tmp_path)


def test_refuses_held_release_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recovery, "validate_root", lambda root: None)
    (tmp_path / "shared").mkdir(mode=0o700)
    monkeypatch.setattr(recovery.fcntl, "flock", lambda *args: (_ for _ in ()).throw(BlockingIOError()))
    monkeypatch.setattr(recovery, "b_minio_id", lambda: pytest.fail("must not touch Docker"))
    with pytest.raises(ValueError, match="lock is held"):
        recovery.backup_and_drill(tmp_path)


def test_no_success_manifest_when_restore_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recovery, "_validate_backup_mount", lambda root: root / "backups")
    monkeypatch.setattr(recovery, "b_minio_id", lambda: "a" * 12)
    monkeypatch.setattr(recovery, "_run", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, recovery.REVISION.encode(), b""))
    monkeypatch.setattr(recovery, "_capture", lambda container, folder: (folder / "iam.zip").write_bytes(b"archive"))
    monkeypatch.setattr(recovery, "_drill", lambda folder: (_ for _ in ()).throw(ValueError("restore failed")))
    root = tmp_path / "b"
    root.mkdir(mode=0o700)
    (root / "backups").mkdir(mode=0o700)
    with pytest.raises(ValueError, match="restore failed"):
        recovery._backup_locked(root)
    assert not list(root.glob("backups/minio/*/recovery-verified.json"))


def test_restoration_uses_scratch_networkless_minio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(recovery, "_run", lambda command, **kwargs: calls.append(command) or subprocess.CompletedProcess(command, 0, b"", b""))
    monkeypatch.setattr(recovery.subprocess, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 1, b"", b""))
    monkeypatch.setattr(recovery.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("do not restore until ready"))
    monkeypatch.setattr(recovery.time, "sleep", lambda _: None)
    with pytest.raises(ValueError, match="did not become ready"):
        recovery._drill(tmp_path)
    start = calls[0]
    assert start[:3] == ["docker", "run", "-d"]
    assert "--network" in start and "none" in start
    assert "--tmpfs" in start and "/data:mode=0700" in start
    assert f"{tmp_path}:/run/backup:ro" in start
    assert not any(item in start for item in ("--publish", "-p", "--privileged"))


def test_direct_script_invocation_imports_repository() -> None:
    result = subprocess.run(["python3", str(recovery.SOURCE_ROOT / "scripts/b_minio_recovery.py"), "--help"],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "--apply" in result.stdout
