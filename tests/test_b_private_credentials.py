"""B credentials are generated once on the target and never printed or rotated."""

from pathlib import Path

import pytest

from scripts import provision_b_private_credentials as provision
from scripts.check_b_pair import SHARED, validate_pair

ROOT = Path(__file__).resolve().parents[1]


def _host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "b"
    for folder in (root, root / "shared", root / "shared/backend", root / "shared/agent"):
        folder.mkdir(mode=0o700)
    backend_template = (ROOT / "env/us-east-uat.env.example").read_text()
    backend = root / "shared/backend/north-america-staging.env"
    backend.write_text(backend_template)
    backend.chmod(0o600)
    # GitHub's Backend-only checkout has no sibling Agent repository. Build a
    # synthetic minimum Agent env from the shared contract; other tests check
    # the real Agent template in its own repository.
    backend_values = _values(backend)
    agent_lines = [f"{key}={backend_values[key]}" for key in SHARED]
    agent_lines += [
        f"{key}={provision._placeholder(key)}" for key in (
            provision.AGENT_SERVICE_KEY, provision.AGENT_ADMIN_KEY, "OPENAI_API_KEY",
        )
    ]
    agent = root / "shared/agent/north-america-staging.env"
    agent.write_text("\n".join(agent_lines) + "\n")
    agent.chmod(0o600)
    lock = root / "shared/north-america-staging-release.lock"
    lock.touch(mode=0o600)
    monkeypatch.setattr(provision, "HOST_ROOT", root)
    monkeypatch.setattr(provision.socket, "gethostname", lambda: provision.TARGET_HOSTNAME)
    return root


def _values(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#"))


def test_generates_b_credentials_once_and_preserves_external_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    root = _host(tmp_path, monkeypatch)
    backend = root / "shared/backend/north-america-staging.env"
    agent = root / "shared/agent/north-america-staging.env"
    assert provision.provision(root) is True
    product, runtime = _values(backend), _values(agent)
    for key in provision.GENERATED_BACKEND:
        assert product[key] and "REPLACE_WITH" not in product[key]
    for key in provision.SHARED_AGENT:
        assert runtime[key] == product[key]
    assert runtime["PRODUCT_BACKEND_SERVICE_KEY"] == product["AGENT_RUNTIME_SERVICE_API_KEY"]
    assert runtime["RUNTIME_ADMIN_SERVICE_KEY"] not in (product["SERVICE_API_KEY"], product["AGENT_RUNTIME_SERVICE_API_KEY"])
    assert len(set(product[key] for key in provision.GENERATED_BACKEND if key != "AUTH_JWT_PRIVATE_KEY_B64")) == len(provision.GENERATED_BACKEND) - 1
    assert product["AUTH_SMTP_PASSWORD"].startswith("REPLACE_WITH_")
    assert product["AUTH_INVITE_CODES"].startswith("REPLACE_WITH_")
    assert runtime["OPENAI_API_KEY"].startswith("REPLACE_WITH_")
    assert backend.stat().st_mode & 0o777 == agent.stat().st_mode & 0o777 == 0o600
    validate_pair(backend, agent)
    from app.core.settings import load_auth_jwt_private_key
    assert load_auth_jwt_private_key(product["AUTH_JWT_PRIVATE_KEY_B64"]).key_size >= 3072
    assert product["MOMCOZY_PRODUCT_POSTGRES_PASSWORD"] not in capsys.readouterr().out
    before = backend.read_bytes(), agent.read_bytes()
    assert provision.provision(root) is False  # no rotation on re-entry
    assert (backend.read_bytes(), agent.read_bytes()) == before


def test_rejects_partially_filled_or_public_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _host(tmp_path, monkeypatch)
    backend = root / "shared/backend/north-america-staging.env"
    agent = root / "shared/agent/north-america-staging.env"
    old = backend.read_bytes(), agent.read_bytes()
    backend.write_text(backend.read_text().replace("MOMCOZY_POSTGRES_ADMIN_PASSWORD=REPLACE_WITH_US_EAST_UAT_MOMCOZY_POSTGRES_ADMIN_PASSWORD", "MOMCOZY_POSTGRES_ADMIN_PASSWORD=preexisting-secret"))
    with pytest.raises(ValueError, match="partially provisioned"):
        provision.provision(root)
    assert agent.read_bytes() == old[1]
    backend.write_bytes(old[0])
    backend.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        provision.provision(root)
    assert agent.read_bytes() == old[1]


def test_rejects_symlink_and_wrong_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _host(tmp_path, monkeypatch)
    backend = root / "shared/backend/north-america-staging.env"
    backend.rename(backend.with_name("original"))
    backend.symlink_to(backend.with_name("original"))
    with pytest.raises(ValueError, match="private"):
        provision.provision(root)
    backend.unlink()
    backend.with_name("original").rename(backend)
    backend.write_text(backend.read_text().replace("MOMCOZY_B_ENV_MARKER=us-east-uat", "MOMCOZY_B_ENV_MARKER=legacy-staging"))
    with pytest.raises(ValueError, match="identity"):
        provision.provision(root)


def test_no_apply_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _host(tmp_path, monkeypatch)
    before = (root / "shared/backend/north-america-staging.env").read_bytes()
    assert provision.main([]) == 0
    assert (root / "shared/backend/north-america-staging.env").read_bytes() == before
