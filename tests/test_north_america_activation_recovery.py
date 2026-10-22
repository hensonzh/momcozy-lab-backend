"""Partial North America activation recovery and loopback isolation regressions."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import north_america_activate_first as release

def test_loopback_listener_parses_local_not_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    class Ready:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(release.urllib.request, "urlopen", lambda *a, **k: Ready())
    monkeypatch.setattr(release.json, "load", lambda response: {"status": "ok"})
    monkeypatch.setattr(release, "_run", lambda command, **kw: (
        "LISTEN 0 4096 127.0.0.1:8001 0.0.0.0:*\n" if command[0] == "ss" else "abc123\n"))
    release.check_ready("backend")


@pytest.mark.parametrize("local", ["0.0.0.0:8001", "[::]:8001", "127.0.0.1:8001\nLISTEN 0 4096 0.0.0.0:8001 0.0.0.0:*"])
def test_ready_rejects_public_listener(local: str, monkeypatch: pytest.MonkeyPatch) -> None:
    class Ready:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(release.urllib.request, "urlopen", lambda *a, **k: Ready())
    monkeypatch.setattr(release.json, "load", lambda response: {"status": "ok"})
    monkeypatch.setattr(release, "_run", lambda command, **kw: (
        "LISTEN 0 4096 " + local + " 0.0.0.0:*\n" if command[0] == "ss" else "abc123\n"))
    with pytest.raises(ValueError, match="not isolated"):
        release.check_ready("backend")


def test_recover_requires_exact_business_provenance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "releases/backend" / ("a" * 40)
    source.mkdir(parents=True)
    (source / "docker-compose.us-east-uat.yml").write_text(
        "services:\n  api:\n  notification-worker:\n  auth-email-worker:\n  stool-photo-review-worker:\n"
    )
    private = tmp_path / "backend.env"
    private.write_text("MOMCOZY_NORTH_AMERICA_ENV_MARKER=us-east-uat\n")
    args = SimpleNamespace(service="backend", source=source, env_file=private,
                           image_id="sha256:" + "b" * 64, local_image="ghcr.io/example/backend:north-america-oci-aaaaaaa")
    seen = []
    running_image = args.image_id
    def run(command, **kwargs):
        if command[0] == "docker" and command[1] == "compose":
            service = command[-1]
            return f"{service} " + "d" * 64 + "\n"
        if command[1] == "ps":
            seen.append(next(part.rsplit("=", 1)[1] for part in command
                             if part.startswith("label=com.docker.compose.service=")))
            return "abc123\n"
        service = seen[-1]
        return json.dumps({"Image": running_image, "State": {"Status": "running", "Health": {"Status": "healthy"}},
                           "Config": {"Labels": {"com.docker.compose.project": "momcozy-lab-backend-us-east-uat",
                                                  "com.docker.compose.service": service,
                                                  "com.docker.compose.project.working_dir": str(source),
                                                  "com.docker.compose.project.config_files": str(source / "docker-compose.us-east-uat.yml"),
                                                  "com.docker.compose.project.environment_file": str(args.env_file),
                                                  "com.docker.compose.config-hash": "d" * 64}}})
    monkeypatch.setattr(release, "_run", run)
    release.check_running_service_provenance(args)
    assert set(seen) == {"api", "notification-worker", "auth-email-worker", "stool-photo-review-worker"}
    args.image_id = "sha256:" + "c" * 64
    seen.clear()
    with pytest.raises(ValueError, match="provenance differs"):
        release.check_running_service_provenance(args)


def test_old_b_release_with_three_workers_remains_verifiable_during_roll_forward(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "releases/backend" / ("a" * 40)
    source.mkdir(parents=True)
    (source / "docker-compose.us-east-uat.yml").write_text(
        "services:\n  api:\n  notification-worker:\n  auth-email-worker:\n"
    )
    private = tmp_path / "backend.env"
    private.write_text("MOMCOZY_B_ENV_MARKER=us-east-uat\n")
    args = SimpleNamespace(service="backend", source=source, env_file=private,
                           image_id="sha256:" + "b" * 64, local_image="backend:old")
    seen = []
    unexpected_review_worker = False
    def run(command, **kwargs):
        if command[0] == "docker" and command[1] == "compose":
            service = command[-1]
            return f"{service} " + "d" * 64 + "\n"
        if command[1] == "ps":
            name = next(part.rsplit("=", 1)[1] for part in command
                        if part.startswith("label=com.docker.compose.service="))
            if name == "stool-photo-review-worker":
                return "abc123\n" if unexpected_review_worker else ""
            seen.append(name)
            return "abc123\n"
        service = seen[-1]
        return json.dumps({"Image": args.image_id, "State": {"Status": "running", "Health": {"Status": "healthy"}},
                           "Config": {"Labels": {"com.docker.compose.project": "momcozy-lab-backend-us-east-uat",
                                                  "com.docker.compose.service": service,
                                                  "com.docker.compose.project.working_dir": str(source),
                                                  "com.docker.compose.project.config_files": str(source / "docker-compose.us-east-uat.yml"),
                                                  "com.docker.compose.project.environment_file": str(private),
                                                  "com.docker.compose.config-hash": "d" * 64}}})
    monkeypatch.setattr(release, "_run", run)
    release.check_running_service_provenance(args)
    assert set(seen) == {"api", "notification-worker", "auth-email-worker"}
    unexpected_review_worker = True
    with pytest.raises(ValueError, match="unexpected.*worker"):
        release.check_running_service_provenance(args)



def test_recover_never_restarts_business_containers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release, "ROOT", tmp_path)
    source = tmp_path / "releases/backend" / ("a" * 40)
    source.mkdir(parents=True)
    (tmp_path / "current").mkdir()
    pg = tmp_path / "backup/postgres"
    pg.mkdir(parents=True)
    (pg / "recovery-verified.json").write_text('{"databases":{"momcozy_lab_backend_uat":{"alembic_revision":"rev"}}}')
    monkeypatch.setattr(release, "check_running_service_provenance", lambda args: None)
    monkeypatch.setattr(release, "check_public_ready", lambda service: None)
    monkeypatch.setattr(release, "check_service_containers_absent", lambda *a: pytest.fail("must not require absence"))
    monkeypatch.setattr(release, "start_services", lambda *a: pytest.fail("must not restart"))
    monkeypatch.setattr(release, "check_ready", lambda *a: None)
    monkeypatch.setattr(release, "read_live_revision", lambda *a: "rev")
    monkeypatch.setattr(release, "_validate_backup_mount", lambda root: pg.parent)
    monkeypatch.setattr(release, "select_latest", lambda root: {"postgres": pg})
    args = SimpleNamespace(service="backend", source=source, commit="a" * 40, image="sha256:abc",
                           image_id="sha256:abc", recover_running=True)
    release.activate(args)
    assert (tmp_path / "current/backend").resolve() == source
