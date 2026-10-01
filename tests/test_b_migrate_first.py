"""B first migration preserves fresh infrastructure and fails closed."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import b_migrate_first as migrate


def test_refuses_existing_release_pointers_before_any_compose(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(migrate, "B_ROOT", tmp_path)
    (tmp_path / "current").mkdir()
    (tmp_path / "current/backend").symlink_to(tmp_path)
    monkeypatch.setattr(migrate, "_compose", lambda *args: pytest.fail("must not mutate"))
    with pytest.raises(ValueError, match="first release"):
        migrate.check_first_release()


def test_image_label_and_id_must_match_requested_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, '{"Id":"sha256:' + "a" * 64 + '","Config":{"Labels":{' +
                                           '"org.opencontainers.image.revision":"wrong",' +
                                           '"org.momcozy.release-target":"north-america-staging"}}}', "")
    monkeypatch.setattr(migrate.subprocess, "run", fake)
    with pytest.raises(ValueError, match="image provenance"):
        migrate.verify_image("ghcr.io/hensonzh/momcozy-lab-backend:b-oci-bbbbbbb", "b" * 40, "backend", "sha256:" + "a" * 64)


def test_migrate_calls_only_one_shot_services(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(migrate, "_compose", lambda *args: commands.append(args[-1]))
    args = SimpleNamespace(backend_source=Path("/b"), agent_source=Path("/a"),
                           backend_env=Path("/be"), agent_env=Path("/ae"),
                           backend_local_image="backend:verified", agent_local_image="agent:verified",
                           backend_commit="b" * 40, agent_commit="c" * 40)
    migrate.run_migrations(args)
    assert len(commands) == 2
    for command in commands:
        assert command[-1] == "migrate"
        assert "--rm" in command and "--no-deps" in command and "--pull" in command and "never" in command
        assert not any(value in command for value in ("api", "worker", "up", "down", "-v"))


def test_first_migration_checks_both_unmigrated_databases(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    def fake(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(command, 0, "a" * 12 + "\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")
    monkeypatch.setattr(migrate.subprocess, "run", fake)
    migrate.check_databases_unmigrated()
    assert len(calls) == 3
    assert {calls[1][-1], calls[2][-1]} == {"MOMCOZY_PRODUCT_POSTGRES_DB", "MOMCOZY_AGENT_POSTGRES_DB"}
    assert "printenv" in calls[1][-3]
    assert all("$MOMCOZY_" not in part for command in calls for part in command)


def test_migrated_database_fails_before_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    def fake(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(command, 0 if calls == 1 else 1, "a" * 12 + "\n" if calls == 1 else "", "")
    monkeypatch.setattr(migrate.subprocess, "run", fake)
    with pytest.raises(ValueError, match="already migrated"):
        migrate.check_databases_unmigrated()
    assert calls == 2


def test_direct_invocation_loads_repository() -> None:
    result = subprocess.run(["python3", str(Path(__file__).resolve().parents[1] / "scripts/b_migrate_first.py"), "--help"],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0
