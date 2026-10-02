"""B ongoing releases switch pointers only after recovery and live readiness."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from scripts import b_update_release as release


def _source(root: Path, service: str, commit: str, *, revision: str = "rev") -> Path:
    path = root / "releases" / service / commit
    path.mkdir(parents=True)
    (path / "release-manifest.json").write_text(json.dumps({
        "deployment_target": "north-america-staging", "service": service,
        "commit": commit, "image_ref": f"ghcr.io/hensonzh/momcozy-lab-{service}@sha256:" + "b" * 64,
        "migration_revision": revision, "local_image_id": "sha256:" + "c" * 64,
    }))
    return path


def test_update_refuses_schema_change_before_any_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "ROOT", tmp_path)
    current = _source(tmp_path, "backend", "a" * 40)
    (tmp_path / "current").mkdir()
    (tmp_path / "current/backend").symlink_to(current)
    args = argparse.Namespace(service="backend", operation="update", commit="d" * 40,
                              source=tmp_path / "releases/backend" / ("d" * 40), env_file=tmp_path / "env",
                              image="ghcr.io/hensonzh/momcozy-lab-backend@sha256:" + "e" * 64,
                              local_image="candidate", image_id="sha256:" + "f" * 64)
    monkeypatch.setattr(release, "admit_target", lambda args: None)
    monkeypatch.setattr(release, "verify_running", lambda args, manifest, source: None)
    monkeypatch.setattr(release, "admit_new", lambda args: None)
    monkeypatch.setattr(release, "read_live_revision", lambda service: "rev")
    monkeypatch.setattr(release, "image_head", lambda tag: "next_rev")
    with pytest.raises(ValueError, match="same schema"):
        release.prepare(args)


def test_update_pointer_switch_follows_recovery_and_readiness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "ROOT", tmp_path)
    old = _source(tmp_path, "agent", "a" * 40)
    new = tmp_path / "releases/agent" / ("d" * 40)
    new.mkdir(parents=True)
    (tmp_path / "current").mkdir()
    (tmp_path / "previous").mkdir()
    (tmp_path / "current/agent").symlink_to(old)
    args = argparse.Namespace(service="agent", operation="update", source=new, commit=new.name,
                              image="ghcr.io/hensonzh/momcozy-lab-agent@sha256:" + "e" * 64,
                              image_id="sha256:" + "f" * 64)
    previous_args = argparse.Namespace(service="agent", source=old, image_id="sha256:" + "c" * 64)
    new_args = argparse.Namespace(service="agent", source=new)
    monkeypatch.setattr(release, "prepare", lambda args: (previous_args, new_args, "rev"))
    calls: list[str] = []
    monkeypatch.setattr(release, "fresh_recovery", lambda source: calls.append("recovered"))
    monkeypatch.setattr(release, "read_live_revision", lambda service: "rev")
    monkeypatch.setattr(release, "verify_running", lambda *args: None)
    def checked(value: argparse.Namespace) -> None:
        assert (tmp_path / "current/agent").resolve() == old
        calls.append("new-ready" if value is new_args else "old-ready")
    monkeypatch.setattr(release, "start_checked", checked)
    release.perform(args)
    assert calls == ["recovered", "new-ready"]
    assert (tmp_path / "current/agent").resolve() == new
    assert (tmp_path / "previous/agent").resolve() == old
    assert json.loads((new / "release-manifest.json").read_text())["migration_revision"] == "rev"


def test_failed_update_restores_old_without_pointer_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "ROOT", tmp_path)
    old = _source(tmp_path, "backend", "a" * 40)
    new = tmp_path / "releases/backend" / ("d" * 40)
    new.mkdir(parents=True)
    (tmp_path / "current").mkdir()
    (tmp_path / "previous").mkdir()
    (tmp_path / "current/backend").symlink_to(old)
    args = argparse.Namespace(service="backend", operation="update", source=new, commit=new.name)
    old_args, new_args = argparse.Namespace(source=old, image_id="sha256:" + "c" * 64), argparse.Namespace(source=new)
    monkeypatch.setattr(release, "prepare", lambda args: (old_args, new_args, "rev"))
    monkeypatch.setattr(release, "fresh_recovery", lambda source: None)
    monkeypatch.setattr(release, "read_live_revision", lambda service: "rev")
    monkeypatch.setattr(release, "verify_running", lambda *args: None)
    calls: list[str] = []
    def checked(value: argparse.Namespace) -> None:
        calls.append("old" if value is old_args else "new")
        if value is new_args:
            raise ValueError("new service unhealthy")
    monkeypatch.setattr(release, "start_checked", checked)
    with pytest.raises(ValueError, match="prior service restored"):
        release.perform(args)
    assert calls == ["new", "old"]
    assert (tmp_path / "current/backend").resolve() == old
    assert not (tmp_path / "previous/backend").exists()
    assert not (new / "release-manifest.json").exists()


def test_rollback_requires_client_confirmation_and_same_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "ROOT", tmp_path)
    old = _source(tmp_path, "backend", "a" * 40, revision="old")
    new = _source(tmp_path, "backend", "d" * 40, revision="new")
    (tmp_path / "current").mkdir()
    (tmp_path / "previous").mkdir()
    (tmp_path / "current/backend").symlink_to(new)
    (tmp_path / "previous/backend").symlink_to(old)
    args = argparse.Namespace(service="backend", operation="rollback", confirm_client_compatible=False, env_file=tmp_path / "env")
    monkeypatch.setattr(release, "admit_target", lambda args: None)
    monkeypatch.setattr(release, "verify_running", lambda args, manifest, source: None)
    monkeypatch.setattr(release, "read_live_revision", lambda service: "new")
    with pytest.raises(ValueError, match="confirmation"):
        release.prepare(args)
    args.confirm_client_compatible = True
    with pytest.raises(ValueError, match="schema"):
        release.prepare(args)


def test_rollback_switches_only_after_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "ROOT", tmp_path)
    current = _source(tmp_path, "agent", "a" * 40)
    previous = _source(tmp_path, "agent", "d" * 40)
    (tmp_path / "current").mkdir()
    (tmp_path / "previous").mkdir()
    (tmp_path / "current/agent").symlink_to(current)
    (tmp_path / "previous/agent").symlink_to(previous)
    args = argparse.Namespace(service="agent", operation="rollback")
    old_args, desired = argparse.Namespace(source=current, image_id="sha256:" + "c" * 64), argparse.Namespace(source=previous)
    monkeypatch.setattr(release, "prepare", lambda args: (old_args, desired, "rev"))
    monkeypatch.setattr(release, "fresh_recovery", lambda source: None)
    monkeypatch.setattr(release, "read_live_revision", lambda service: "rev")
    monkeypatch.setattr(release, "verify_running", lambda *args: None)
    def checked(value: argparse.Namespace) -> None:
        assert value is desired
        assert (tmp_path / "current/agent").resolve() == current
    monkeypatch.setattr(release, "start_checked", checked)
    release.perform(args)
    assert (tmp_path / "current/agent").resolve() == previous
    assert (tmp_path / "previous/agent").resolve() == current


def test_schema_race_after_backup_never_starts_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "prepare", lambda args: (argparse.Namespace(source=tmp_path, image_id="sha256:" + "c" * 64), object(), "rev"))
    monkeypatch.setattr(release, "fresh_recovery", lambda source: None)
    monkeypatch.setattr(release, "read_live_revision", lambda service: "changed")
    monkeypatch.setattr(release, "_slot", lambda service, slot: (tmp_path, {"local_image_id": "sha256:" + "c" * 64}))
    monkeypatch.setattr(release, "start_checked", lambda args: pytest.fail("schema drift must prevent switch"))
    with pytest.raises(ValueError, match="during backup"):
        release.perform(argparse.Namespace(service="backend"))


def test_preflight_only_calls_read_only_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(release, "require_local_docker", lambda: None)
    monkeypatch.setattr(release, "prepare", lambda args: calls.append(args.operation))
    monkeypatch.setattr(release, "perform", lambda args: pytest.fail("preflight must not mutate"))
    assert release.main(["--operation", "update", "--service", "backend"]) == 0
    assert calls == ["update"]


def test_image_head_requires_one_offline_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    seen = []
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, "rev (head)\n", "")
    monkeypatch.setattr(release.subprocess, "run", run)
    assert release.image_head("b:verified") == "rev"
    assert seen[0][0:7] == ["docker", "run", "--rm", "--network", "none", "--pull", "never"]
    monkeypatch.setattr(release.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "a (head)\nb (head)\n", ""))
    with pytest.raises(ValueError, match="single Alembic head"):
        release.image_head("b:verified")
