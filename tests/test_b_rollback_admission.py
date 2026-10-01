"""Read-only B rollback checks real B symlink shape and live DB revision."""

import json
import subprocess
from pathlib import Path

import pytest

from scripts.check_b_rollback import read_live_revision, validate_rollback_pair


def _manifest(root: Path, service: str, slot: str, revision: str = "abc123") -> Path:
    commit = ("a" if slot == "current" else "b") * 40
    target = root / "releases" / service / commit
    target.mkdir(parents=True, exist_ok=True)
    manifest = target / "release-manifest.json"
    manifest.write_text(json.dumps({
        "deployment_target": "north-america-staging", "service": service,
        "commit": commit, "image_ref": f"ghcr.io/hensonzh/momcozy-lab-{service}@sha256:" + "c" * 64,
        "migration_revision": revision,
    }))
    link = root / slot / service
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    return link / "release-manifest.json"


def test_rollback_pair_requires_schema_match_and_client_confirmation(tmp_path: Path) -> None:
    current = _manifest(tmp_path, "backend", "current")
    previous = _manifest(tmp_path, "backend", "previous")
    with pytest.raises(ValueError, match="confirmation"):
        validate_rollback_pair(tmp_path, current, previous, "backend", False, database_revision="abc123")
    validate_rollback_pair(tmp_path, current, previous, "backend", True, database_revision="abc123")
    for revision in ("", "older"):
        with pytest.raises(ValueError, match="live database revision"):
            validate_rollback_pair(tmp_path, current, previous, "backend", True, database_revision=revision)


def test_rollback_pair_rejects_schema_change_even_with_confirmation(tmp_path: Path) -> None:
    current = _manifest(tmp_path, "agent", "current")
    previous = _manifest(tmp_path, "agent", "previous", "older")
    with pytest.raises(ValueError, match="schema"):
        validate_rollback_pair(tmp_path, current, previous, "agent", True, database_revision="abc123")


@pytest.mark.parametrize("tamper", ["target", "image", "escape", "symlink_manifest", "commit"])
def test_rollback_pair_rejects_cross_lane_or_untrusted_manifests(tmp_path: Path, tamper: str) -> None:
    current = _manifest(tmp_path, "backend", "current")
    previous = _manifest(tmp_path, "backend", "previous")
    target = previous.parent.resolve()
    manifest = target / "release-manifest.json"
    if tamper == "escape":
        previous.parent.unlink()
        previous.parent.symlink_to(tmp_path / "outside")
    elif tamper == "symlink_manifest":
        manifest.unlink()
        manifest.symlink_to(current)
    else:
        data = json.loads(manifest.read_text())
        if tamper == "target":
            data["deployment_target"] = "legacy-staging"
        elif tamper == "image":
            data["image_ref"] = "ghcr.io/hensonzh/momcozy-lab-agent@sha256:" + "c" * 64
        else:
            data["commit"] = "d" * 40
        manifest.write_text(json.dumps(data))
    with pytest.raises((ValueError, OSError)):
        validate_rollback_pair(tmp_path, current, previous, "backend", True, database_revision="abc123")


def test_live_revision_filters_only_b_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        output = "a" * 12 + "\n" if len(calls) == 1 else "abc123\n"
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(subprocess, "run", run)
    assert read_live_revision("agent") == "abc123"
    assert "label=com.docker.compose.project=momcozy-lab-backend-us-east-uat" in calls[0]
    assert "label=com.docker.compose.service=postgres" in calls[0]
    assert "MOMCOZY_AGENT_POSTGRES_DB" in calls[1][-1]


def test_rollback_cli_rejects_unapproved_root_before_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.check_b_rollback import main
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("docker must not run"))
    assert main(["--release-root", str(tmp_path), "--service", "backend", "--confirm-client-compatible"]) == 1


def test_live_revision_rejects_ambiguous_b_containers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "a" * 12 + "\n" + "b" * 12 + "\n", "")
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ValueError, match="exactly one"):
        read_live_revision("backend")
    assert len(calls) == 1


def test_live_revision_rejects_empty_or_malformed_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    results = iter(("a" * 12 + "\n", "bad\nrevision\n"))
    def run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, next(results), "")
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ValueError, match="single safe"):
        read_live_revision("agent")
