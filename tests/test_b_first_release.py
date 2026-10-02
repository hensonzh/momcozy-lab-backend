"""First B deployment stages state/restore before business activation, never auto-rolls back."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import b_first_release as release


def test_first_release_is_dry_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "run_first_release", lambda args: pytest.fail("must not mutate without --apply"))
    monkeypatch.setattr(release, "read_only_preflight", lambda args: pytest.fail("check only on explicit --preflight"))
    assert release.main(["--backend-source", "/b", "--agent-source", "/a"]) == 0


def test_explicit_preflight_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(release, "require_local_docker", lambda: calls.append("local-docker"))
    monkeypatch.setattr(release, "read_only_preflight", lambda args: calls.append("admission"), raising=False)
    monkeypatch.setattr(release, "run_first_release", lambda args: pytest.fail("must not mutate"))
    assert release.main(["--preflight", "--backend-source", "/b", "--agent-source", "/a"]) == 0
    assert calls == ["local-docker", "admission"]


def _pair() -> SimpleNamespace:
    return SimpleNamespace(
        backend_source=Path("/synthetic/backend"), agent_source=Path("/synthetic/agent"),
        backend_env=Path("/synthetic/backend.env"), agent_env=Path("/synthetic/agent.env"),
        backend_target=Path("/synthetic/backend.json"), agent_target=Path("/synthetic/agent.json"),
        backend_commit="a" * 40, agent_commit="b" * 40,
        backend_image="ghcr.io/example/backend@sha256:" + "a" * 64,
        agent_image="ghcr.io/example/agent@sha256:" + "b" * 64,
        backend_local_image="backend:verified", agent_local_image="agent:verified",
        backend_image_id="sha256:" + "a" * 64, agent_image_id="sha256:" + "b" * 64,
    )


def test_first_release_orders_recovery_and_product_before_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(release, "preflight", lambda args: calls.append("preflight"))
    monkeypatch.setattr(release, "check_fresh", lambda args: calls.append("fresh"))
    monkeypatch.setattr(release, "check_ingress_tls", lambda service: calls.append("tls-" + service))
    monkeypatch.setattr(release, "verify_stateful_images", lambda: calls.append("stateful-images"))
    monkeypatch.setattr(release, "run_stage", lambda name, argv: calls.append(name))
    monkeypatch.setattr(release, "select_latest", lambda root: calls.append("recovery-evidence") or {"postgres": Path("/new/postgres"), "redis": Path("/new/redis"), "minio": Path("/new/minio")})
    monkeypatch.setattr(release, "_validate_backup_mount", lambda root: Path("/synthetic-b-backup"))
    monkeypatch.setattr(release, "existing_recovery_markers", lambda root: set())
    monkeypatch.setattr(release, "check_public", lambda service: calls.append("public-" + service))
    release.run_first_release(_pair())
    assert calls == ["preflight", "fresh", "tls-backend", "tls-agent", "stateful-images", "bootstrap", "migrate", "postgres-recovery", "redis-recovery",
                     "minio-recovery", "recovery-evidence", "backend-activate", "public-backend",
                     "agent-activate", "public-agent"]


def test_first_release_stops_after_failed_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(release, "preflight", lambda args: None)
    monkeypatch.setattr(release, "check_fresh", lambda args: None)
    monkeypatch.setattr(release, "check_ingress_tls", lambda service: None)
    monkeypatch.setattr(release, "verify_stateful_images", lambda: None)
    monkeypatch.setattr(release, "_validate_backup_mount", lambda root: Path("/synthetic-b-backup"))
    monkeypatch.setattr(release, "existing_recovery_markers", lambda root: set())
    def stage(name: str, argv: list[str]) -> None:
        calls.append(name)
        if name == "redis-recovery":
            raise ValueError("failed")
    monkeypatch.setattr(release, "run_stage", stage)
    with pytest.raises(ValueError):
        release.run_first_release(_pair())
    assert calls == ["bootstrap", "migrate", "postgres-recovery", "redis-recovery"]


def test_untrusted_public_ingress_blocks_state_bootstrap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "preflight", lambda args: None)
    monkeypatch.setattr(release, "check_fresh", lambda args: None)
    monkeypatch.setattr(release, "check_ingress_tls", lambda service: (_ for _ in ()).throw(ValueError("TLS unavailable")))
    monkeypatch.setattr(release, "run_stage", lambda name, argv: pytest.fail("no stateful service may start"))
    with pytest.raises(ValueError, match="TLS unavailable"):
        release.run_first_release(_pair())


def test_stale_recovery_evidence_cannot_unlock_activation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(release, "preflight", lambda args: None)
    monkeypatch.setattr(release, "check_fresh", lambda args: None)
    monkeypatch.setattr(release, "check_ingress_tls", lambda service: None)
    monkeypatch.setattr(release, "verify_stateful_images", lambda: None)
    monkeypatch.setattr(release, "_validate_backup_mount", lambda root: Path("/synthetic-b-backup"))
    monkeypatch.setattr(release, "existing_recovery_markers", lambda root: {Path("/old/postgres/recovery-verified.json"), Path("/old/redis/recovery-verified.json"), Path("/old/minio/recovery-verified.json")})
    monkeypatch.setattr(release, "select_latest", lambda root: {"postgres": Path("/old/postgres"), "redis": Path("/old/redis"), "minio": Path("/old/minio")})
    monkeypatch.setattr(release, "run_stage", lambda name, argv: calls.append(name))
    with pytest.raises(ValueError, match="not fresh"):
        release.run_first_release(_pair())
    assert "backend-activate" not in calls


def test_stateful_image_identity_blocks_first_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "preflight", lambda args: None)
    monkeypatch.setattr(release, "check_fresh", lambda args: None)
    monkeypatch.setattr(release, "check_ingress_tls", lambda service: None)
    monkeypatch.setattr(release, "verify_stateful_images", lambda: None)
    monkeypatch.setattr(release, "_validate_backup_mount", lambda root: Path("/synthetic-b-backup"))
    monkeypatch.setattr(release, "existing_recovery_markers", lambda root: set())
    monkeypatch.setattr(release, "verify_stateful_images", lambda: (_ for _ in ()).throw(ValueError("bad image")), raising=False)
    monkeypatch.setattr(release, "run_stage", lambda name, argv: pytest.fail("no B bootstrap with wrong stateful image"))
    with pytest.raises(ValueError, match="bad image"):
        release.run_first_release(_pair())


def test_stateful_images_compare_approved_digest_and_arch(monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    import subprocess

    from scripts.b_postgres_recovery import POSTGRES_IMAGE
    from scripts.b_redis_recovery import REDIS_IMAGE
    from scripts.b_minio_recovery import IMAGE as MINIO_IMAGE, REVISION

    seen = []
    def inspect(command, **kwargs):
        seen.append(command)
        image = command[5]
        data = {"Os": "linux", "Architecture": "amd64", "RepoDigests": [], "Config": {"Labels": {}}}
        if image == MINIO_IMAGE:
            data["Config"]["Labels"] = {"org.opencontainers.image.revision": REVISION}
        elif image in (POSTGRES_IMAGE, REDIS_IMAGE):
            data["RepoDigests"] = [image.split(":", 1)[0].split("@", 1)[0] + "@" + image.rsplit("@", 1)[1]]
        return subprocess.CompletedProcess(command, 0, json.dumps(data), "")

    monkeypatch.setattr(release.subprocess, "run", inspect)
    release.verify_stateful_images()
    assert len(seen) == 3
    assert all(command[3:5] == ["--platform", "linux/amd64"] for command in seen)

    def wrong_digest(command, **kwargs):
        result = inspect(command, **kwargs)
        if command[5] == POSTGRES_IMAGE:
            bad = json.loads(result.stdout)
            bad["RepoDigests"] = ["postgres@sha256:" + "0" * 64]
            return subprocess.CompletedProcess(command, 0, json.dumps(bad), "")
        return result
    monkeypatch.setattr(release.subprocess, "run", wrong_digest)
    with pytest.raises(ValueError, match="approved digest"):
        release.verify_stateful_images()


def test_first_release_apply_serialized_without_reusing_stage_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "ROOT", tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o700)
    calls = []
    monkeypatch.setattr(release, "run_first_release", lambda args: calls.append("ran"))
    args = _pair()
    with release.first_release_lock():
        lock_file = shared / "north-america-staging-first-release.lock"
        assert lock_file.is_file() and lock_file.stat().st_mode & 0o777 == 0o600
        with pytest.raises(ValueError, match="first-release lock"):
            with release.first_release_lock():
                pytest.fail("concurrent runner must not enter")
        release.run_first_release(args)
    assert calls == ["ran"]
    with release.first_release_lock():
        pass


def test_root_operator_can_lock_ubuntu_owned_private_b_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """B host Docker requires sudo, while its 0700 release root belongs to ubuntu."""
    monkeypatch.setattr(release, "ROOT", tmp_path)
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o700)
    monkeypatch.setattr(release.os, "geteuid", lambda: 0)
    with release.first_release_lock():
        assert (shared / "north-america-staging-first-release.lock").is_file()
