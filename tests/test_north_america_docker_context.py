"""North America host operations must not inspect or mutate Docker via inherited remote context."""

from pathlib import Path

import pytest

from scripts.north_america_docker_context import require_local_docker


@pytest.mark.parametrize("name,value", [
    ("DOCKER_HOST", "tcp://a-host:2375"),
    ("DOCKER_CONTEXT", "a-context"),
    ("DOCKER_CONFIG", "/another/user/.docker"),
])
def test_refuses_inherited_docker_target(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="Docker target"):
        require_local_docker()


def test_accepts_local_socket_when_no_override(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    require_local_docker()


def test_all_mutating_b_entrypoints_require_local_docker() -> None:
    root = Path(__file__).resolve().parents[1] / "scripts"
    for name in ("north_america_first_release.py", "north_america_bootstrap_infra.py", "north_america_migrate_first.py",
                 "north_america_activate_first.py", "north_america_postgres_recovery.py",
                 "north_america_redis_recovery.py", "north_america_minio_recovery.py"):
        text = (root / name).read_text()
        assert "require_local_docker()" in text
        if "backup_and_drill(ROOT)" in text:
            assert text.index("require_local_docker()") < text.rfind("backup_and_drill(ROOT)")


def test_direct_host_scripts_keep_cli_importable() -> None:
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    for name in ("north_america_first_release.py", "north_america_postgres_recovery.py", "north_america_redis_recovery.py",
                 "north_america_minio_recovery.py", "check_north_america_fresh_bootstrap.py", "check_north_america_rollback.py"):
        if name == "check_north_america_fresh_bootstrap.py":
            continue  # no --help CLI; separately validated by its unit suite
        result = subprocess.run([sys.executable, str(root / "scripts" / name), "--help"],
                                capture_output=True, text=True, check=False)
        assert result.returncode == 0, name
