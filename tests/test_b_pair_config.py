"""Cross-service B credentials and origins must agree without printing values."""

from pathlib import Path

import pytest

from scripts.check_b_pair import validate_pair

ROOT = Path(__file__).resolve().parents[1]
AGENT_TEMPLATE = """MOMCOZY_B_ENV_MARKER=us-east-uat
MOMCOZY_BACKEND_COMPOSE_PROJECT=momcozy-lab-backend-us-east-uat
MOMCOZY_AGENT_COMPOSE_PROJECT=momcozy-lab-agent-us-east-uat
MOMCOZY_NETWORK_NAME=momcozy-lab-us-east-uat
MOMCOZY_BACKEND_API_BIND=127.0.0.1:8101
MOMCOZY_AGENT_API_BIND=127.0.0.1:8102
MOMCOZY_BACKEND_PUBLIC_URL=https://backend-us-dev.lute-momcozylab.luteos.cloud
MOMCOZY_AGENT_PUBLIC_URL=https://agent-us-dev.lute-momcozylab.luteos.cloud
MOMCOZY_POSTGRES_ADMIN_USER=momcozy_us_east_uat_admin
MOMCOZY_AGENT_POSTGRES_DB=momcozy_lab_agent_uat
MOMCOZY_AGENT_POSTGRES_USER=momcozy_lab_agent_uat
MOMCOZY_AGENT_POSTGRES_PASSWORD=synthetic-MOMCOZY_AGENT_POSTGRES_PASSWORD
MOMCOZY_AGENT_MINIO_BUCKET=momcozy-agent-us-east-uat
MOMCOZY_AGENT_MINIO_ACCESS_KEY=synthetic-MOMCOZY_AGENT_MINIO_ACCESS_KEY
MOMCOZY_AGENT_MINIO_SECRET_KEY=synthetic-MOMCOZY_AGENT_MINIO_SECRET_KEY
MOMCOZY_AGENT_REDIS_PASSWORD=synthetic-MOMCOZY_AGENT_REDIS_PASSWORD
AUTH_JWT_ISSUER=momcozy-product-us-east-uat
PRODUCT_BACKEND_SERVICE_KEY=synthetic-AGENT_RUNTIME_SERVICE_API_KEY
"""


def _env(tmp_path: Path, name: str) -> Path:
    data = (ROOT / "env/us-east-uat.env.example").read_text() if name == "backend.env" else AGENT_TEMPLATE
    data = data.replace("REPLACE_WITH_US_EAST_UAT_", "synthetic-")
    path = tmp_path / name
    path.write_text(data)
    path.chmod(0o600)
    return path


def test_pair_accepts_shared_b_contract(tmp_path: Path) -> None:
    backend = _env(tmp_path, "backend.env")
    agent = _env(tmp_path, "agent.env")
    # The two templates intentionally use differently named placeholder keys;
    # the host must install exactly one common value for this service identity.
    agent.write_text(agent.read_text().replace(
        "PRODUCT_BACKEND_SERVICE_KEY=synthetic-PRODUCT_BACKEND_SERVICE_KEY",
        "PRODUCT_BACKEND_SERVICE_KEY=synthetic-AGENT_RUNTIME_SERVICE_API_KEY",
    ))
    validate_pair(backend, agent)


@pytest.mark.parametrize("key", [
    "MOMCOZY_AGENT_POSTGRES_PASSWORD", "MOMCOZY_AGENT_REDIS_PASSWORD",
    "MOMCOZY_AGENT_MINIO_ACCESS_KEY", "MOMCOZY_AGENT_MINIO_SECRET_KEY",
    "AUTH_JWT_ISSUER", "MOMCOZY_BACKEND_PUBLIC_URL", "MOMCOZY_AGENT_PUBLIC_URL",
    "PRODUCT_BACKEND_SERVICE_KEY",
])
def test_pair_rejects_mismatch_without_value_in_error(tmp_path: Path, key: str) -> None:
    backend = _env(tmp_path, "backend.env")
    agent = _env(tmp_path, "agent.env")
    agent.write_text(agent.read_text().replace(
        "PRODUCT_BACKEND_SERVICE_KEY=synthetic-PRODUCT_BACKEND_SERVICE_KEY",
        "PRODUCT_BACKEND_SERVICE_KEY=synthetic-AGENT_RUNTIME_SERVICE_API_KEY",
    ))
    lines = agent.read_text().splitlines()
    agent.write_text("\n".join(line if not line.startswith(key + "=") else key + "=synthetic-secret-not-to-print" for line in lines) + "\n")
    with pytest.raises(ValueError) as failure:
        validate_pair(backend, agent)
    assert "synthetic-secret-not-to-print" not in str(failure.value)


def test_pair_rejects_public_env_file(tmp_path: Path) -> None:
    backend = _env(tmp_path, "backend.env")
    agent = _env(tmp_path, "agent.env")
    backend.chmod(0o644)
    with pytest.raises(ValueError):
        validate_pair(backend, agent)


def test_pair_rejects_malformed_and_duplicate_env_without_leak(tmp_path: Path) -> None:
    backend = _env(tmp_path, "backend.env")
    agent = _env(tmp_path, "agent.env")
    for extra in ("MOMCOZY_B_ENV_MARKER=us-east-uat\n", "not-a-key-value\n"):
        agent.write_text(AGENT_TEMPLATE + extra)
        with pytest.raises(ValueError) as failure:
            validate_pair(backend, agent)
        assert "REPLACE_WITH_US_EAST_UAT" not in str(failure.value)


def test_pair_rejects_symlinked_parent(tmp_path: Path) -> None:
    backend = _env(tmp_path, "backend.env")
    agent = _env(tmp_path, "agent.env")
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path)
    with pytest.raises(ValueError):
        validate_pair(alias / backend.name, agent)


def test_pair_rejects_shared_placeholder_even_if_both_sides_match(tmp_path: Path) -> None:
    backend = _env(tmp_path, "backend.env")
    agent = _env(tmp_path, "agent.env")
    old = "synthetic-MOMCOZY_AGENT_POSTGRES_PASSWORD"
    backend.write_text(backend.read_text().replace(old, "REPLACE_WITH_US_EAST_UAT_PASSWORD"))
    agent.write_text(agent.read_text().replace(old, "REPLACE_WITH_US_EAST_UAT_PASSWORD"))
    with pytest.raises(ValueError, match="placeholder"):
        validate_pair(backend, agent)
