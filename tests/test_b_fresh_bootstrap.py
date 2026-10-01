"""First B bootstrap is allowed only against an untouched B Docker state."""

import subprocess

import pytest

from scripts import check_b_fresh_bootstrap as bootstrap


def _docker(monkeypatch: pytest.MonkeyPatch, *, volumes: str = "", networks: str = "", containers: str = "") -> list[list[str]]:
    calls: list[list[str]] = []

    def fake(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert command[0] == "docker"
        output = "" if "--filter" in command else {"volume": volumes, "network": networks, "ps": containers}[command[1]]
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(bootstrap.subprocess, "run", fake)
    return calls


def test_accepts_only_fresh_b_state(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _docker(monkeypatch, volumes="unrelated_data\n", networks="bridge\n", containers="legacy-api\n")
    bootstrap.validate_fresh()
    assert any(command[1:3] == ["volume", "ls"] for command in calls)
    assert any(command[1:3] == ["network", "ls"] for command in calls)
    assert any(command[1:3] == ["ps", "-a"] for command in calls)


@pytest.mark.parametrize("resource", ["volume", "network", "ps"])
def test_refuses_label_owned_b_resource_with_unusual_name(monkeypatch: pytest.MonkeyPatch, resource: str) -> None:
    def fake(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        output = "custom-name\n" if command[1] == resource and "--filter" in command else ""
        return subprocess.CompletedProcess(command, 0, output, "")
    monkeypatch.setattr(bootstrap.subprocess, "run", fake)
    with pytest.raises(ValueError, match="B Docker state already exists"):
        bootstrap.validate_fresh()


@pytest.mark.parametrize("volumes,networks,containers", [
    ("momcozy-lab-backend-us-east-uat_postgres_data\n", "", ""),
    ("momcozy-lab-backend-us-east-uat_minio_data\n", "", ""),
    ("momcozy-lab-backend-us-east-uat_redis_data\n", "", ""),
    ("", "momcozy-lab-us-east-uat\n", ""),
    ("", "", "momcozy-lab-backend-us-east-uat-postgres-1\n"),
    ("", "", "momcozy-lab-agent-us-east-uat-api-1\n"),
])
def test_refuses_any_existing_b_state(monkeypatch: pytest.MonkeyPatch, volumes: str, networks: str, containers: str) -> None:
    _docker(monkeypatch, volumes=volumes, networks=networks, containers=containers)
    with pytest.raises(ValueError, match="B Docker state already exists"):
        bootstrap.validate_fresh()


def test_fails_closed_when_docker_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*args: object, **kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, ["docker", "volume", "ls"])
    monkeypatch.setattr(bootstrap.subprocess, "run", unavailable)
    assert bootstrap.main() == 1
