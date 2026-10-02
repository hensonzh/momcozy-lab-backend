"""First B activation never promotes a failed service or touches stateful volumes."""

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import b_activate_first as release


def test_activation_only_targets_business_services(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(release, "_run", lambda command, **kwargs: calls.append(command) or "")
    private = tmp_path / "b.env"
    private.write_text("MOMCOZY_B_ENV_MARKER=us-east-uat\n")
    args = SimpleNamespace(service="backend", source=Path("/b"), env_file=private,
                           local_image="b:verified", commit="a" * 40)
    release.start_services(args)
    assert calls
    command = calls[0]
    assert "--no-deps" in command and "--pull" in command and "never" in command
    assert command[-3:] == ["api", "notification-worker", "auth-email-worker"]
    assert not any(name in command for name in ("postgres", "redis", "minio", "down", "-v"))


def test_no_pointer_if_readiness_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "ROOT", tmp_path)
    (tmp_path / "current").mkdir()
    monkeypatch.setattr(release, "start_services", lambda args: None)
    monkeypatch.setattr(release, "check_ready", lambda *args: (_ for _ in ()).throw(ValueError("not ready")))
    source = tmp_path / "releases/backend" / ("a" * 40)
    source.mkdir(parents=True)
    monkeypatch.setattr(release, "check_service_containers_absent", lambda service: None)
    monkeypatch.setattr(release, "read_live_revision", lambda service: "rev")
    monkeypatch.setattr(release, "_validate_backup_mount", lambda root: tmp_path / "backup")
    pg = tmp_path / "backup/postgres"
    pg.mkdir(parents=True)
    (pg / "recovery-verified.json").write_text('{"databases":{"momcozy_lab_backend_uat":{"alembic_revision":"rev"}}}')
    monkeypatch.setattr(release, "select_latest", lambda root: {"postgres": pg})
    args = SimpleNamespace(service="backend", source=source, commit="a" * 40,
                           image="ghcr.io/hensonzh/momcozy-lab-backend@sha256:" + "b" * 64)
    with pytest.raises(ValueError, match="not ready"):
        release.activate(args)
    assert not (tmp_path / "current/backend").exists()


def test_refuses_pointer_or_manifest_before_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "ROOT", tmp_path)
    (tmp_path / "current").mkdir()
    (tmp_path / "current/backend").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="first release"):
        release.check_fresh_pointer("backend", tmp_path / "releases/backend" / ("a" * 40))


def test_direct_entrypoint_imports() -> None:
    result = subprocess.run(["python3", str(Path(__file__).resolve().parents[1] / "scripts/b_activate_first.py"), "--help"],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0


def test_refuses_stale_postgres_restore_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "ROOT", tmp_path)
    source = tmp_path / "releases/backend" / ("a" * 40)
    source.mkdir(parents=True)
    (tmp_path / "current").mkdir()
    monkeypatch.setattr(release, "check_service_containers_absent", lambda service: None)
    monkeypatch.setattr(release, "read_live_revision", lambda service: "new")
    monkeypatch.setattr(release, "_validate_backup_mount", lambda root: tmp_path / "backup")
    run = tmp_path / "backup/postgres"
    run.mkdir(parents=True)
    (run / "recovery-verified.json").write_text('{"databases":{"momcozy_lab_backend_uat":{"alembic_revision":"old"}}}')
    monkeypatch.setattr(release, "select_latest", lambda root: {"postgres": run})
    monkeypatch.setattr(release, "start_services", lambda args: pytest.fail("must not start with stale backup"))
    args = SimpleNamespace(service="backend", source=source, commit="a" * 40)
    with pytest.raises(ValueError, match="schema differs"):
        release.activate(args)


def test_agent_requires_product_pointer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="Product release"):
        release.check_fresh_pointer("agent", tmp_path / "releases/agent" / ("a" * 40))


def test_verified_on_host_recovery_allows_activation_without_off_host_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(release, "ROOT", tmp_path)
    source = tmp_path / "releases/backend" / ("a" * 40)
    source.mkdir(parents=True)
    (tmp_path / "current").mkdir()
    backups = tmp_path / "backups"

    def run(kind: str, artifacts: dict[str, bytes], manifest: dict) -> None:
        folder = backups / kind / "run"
        folder.mkdir(parents=True, mode=0o700)
        for name, content in artifacts.items():
            (folder / name).write_bytes(content)
        (folder / "recovery-verified.json").write_text(json.dumps({
            "target": "north-america-staging", **manifest,
        }))

    databases = ("momcozy_lab_backend_uat", "momcozy_lab_agent_uat")
    run("postgres", {f"{db}.dump": db.encode() for db in databases}, {
        "verification": "isolated-postgres-restore",
        "databases": {db: {"file": f"{db}.dump", "sha256": hashlib.sha256(db.encode()).hexdigest(),
                           "alembic_revision": "rev"} for db in databases},
    })
    run("redis", {"dump.rdb": b"rdb"}, {
        "verification": "isolated-redis-rdb-restore", "rdb_file": "dump.rdb",
        "sha256": hashlib.sha256(b"rdb").hexdigest(), "key_count": 0,
    })
    run("minio", {"iam.zip": b"iam"}, {
        "verification": "isolated-minio-two-bucket-iam-restore",
        "iam_sha256": hashlib.sha256(b"iam").hexdigest(),
        "objects": {"product": {}, "agent": {}},
    })
    for bucket in ("product", "agent"):
        (backups / "minio/run" / bucket).mkdir()

    monkeypatch.setattr(release, "_validate_backup_mount", lambda root: backups)
    monkeypatch.setattr(release, "read_live_revision", lambda service: "rev")
    monkeypatch.setattr(release, "check_service_containers_absent", lambda service: None)
    monkeypatch.setattr(release, "start_services", lambda args: None)
    monkeypatch.setattr(release, "check_ready", lambda service: None)
    monkeypatch.setattr(release, "check_running_service_provenance", lambda args: None)
    monkeypatch.setattr(release, "check_public_ready", lambda service: None)
    args = SimpleNamespace(service="backend", source=source, commit="a" * 40,
                           image="ghcr.io/example/backend@sha256:" + "b" * 64,
                           image_id="sha256:" + "c" * 64)
    release.activate(args)
    assert (tmp_path / "current/backend").resolve() == source
    assert (source / "release-manifest.json").is_file()
    assert not list(tmp_path.rglob("*.cms"))  # No off-host receipt is required.


def test_first_activation_requires_public_ready_before_pointer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "ROOT", tmp_path)
    source = tmp_path / "releases/backend" / ("a" * 40)
    source.mkdir(parents=True)
    (tmp_path / "current").mkdir()
    pg = tmp_path / "backup/postgres"
    pg.mkdir(parents=True)
    (pg / "recovery-verified.json").write_text('{"databases":{"momcozy_lab_backend_uat":{"alembic_revision":"rev"}}}')
    monkeypatch.setattr(release, "_validate_backup_mount", lambda root: pg.parent)
    monkeypatch.setattr(release, "select_latest", lambda root: {"postgres": pg})
    monkeypatch.setattr(release, "check_service_containers_absent", lambda service: None)
    monkeypatch.setattr(release, "read_live_revision", lambda service: "rev")
    monkeypatch.setattr(release, "start_services", lambda args: None)
    monkeypatch.setattr(release, "check_ready", lambda service: None)
    monkeypatch.setattr(release, "check_running_service_provenance", lambda args: None)
    monkeypatch.setattr(release, "check_public_ready", lambda service: (_ for _ in ()).throw(ValueError("public not ready")))
    args = SimpleNamespace(service="backend", source=source, commit="a" * 40,
                           image="ghcr.io/example/backend@sha256:" + "b" * 64,
                           image_id="sha256:" + "c" * 64)
    with pytest.raises(ValueError, match="public not ready"):
        release.activate(args)
    assert not (tmp_path / "current/backend").exists()
    assert not (source / "release-manifest.json").exists()
