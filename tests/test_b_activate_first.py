"""First B activation never promotes a failed service or touches stateful volumes."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import b_activate_first as release


def test_activation_only_targets_business_services(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(release, "_run", lambda command, **kwargs: calls.append(command) or "")
    args = SimpleNamespace(service="backend", source=Path("/b"), env_file=Path("/env"),
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
