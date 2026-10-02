"""Fresh B infrastructure startup must fail closed before state mutation."""

from pathlib import Path

import pytest
import subprocess

from scripts import b_bootstrap_infra


def test_refuses_existing_b_state_before_compose(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(b_bootstrap_infra, "validate_fresh", lambda: (_ for _ in ()).throw(ValueError("existing")))
    monkeypatch.setattr(b_bootstrap_infra, "_run", lambda command, **kwargs: calls.append(command))
    with pytest.raises(ValueError, match="existing"):
        b_bootstrap_infra.start_fresh(Path("/approved"), Path("/private.env"), "ghcr.io/hensonzh/momcozy-lab-backend@sha256:" + "a" * 64)
    assert not calls


def test_start_only_b_infra_and_bucket_init(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(b_bootstrap_infra, "validate_fresh", lambda: None)
    monkeypatch.setattr(b_bootstrap_infra, "_run", lambda command, **kwargs: calls.append(command))
    private = tmp_path / "private.env"
    private.write_text("MOMCOZY_B_ENV_MARKER=us-east-uat\n")
    b_bootstrap_infra.start_fresh(Path("/approved"), private, "ghcr.io/hensonzh/momcozy-lab-backend@sha256:" + "a" * 64)
    assert len(calls) == 2
    assert calls[0][-6:] == ["up", "-d", "--wait", "postgres", "redis", "minio"]
    assert calls[1][-4:] == ["tools", "run", "--rm", "minio-init"]
    assert all("down" not in call and "-v" not in call for call in calls)
    assert all("api" not in call and "migrate" not in call for call in calls)


def test_direct_script_invocation_loads_repository_modules() -> None:
    result = subprocess.run(
        ["python3", str(Path(__file__).resolve().parents[1] / "scripts/b_bootstrap_infra.py"), "--help"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "--apply" in result.stdout
