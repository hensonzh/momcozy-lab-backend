from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_delivery_contract_uses_canonical_environment_names() -> None:
    assert (ROOT / "docker-compose.deploy.yml").is_file()
    assert (ROOT / "env" / "staging.env.example").is_file()
    assert (ROOT / "env" / "production.env.example").is_file()
    assert (ROOT / "scripts" / "release.py").is_file()
    assert (ROOT / ".github" / "workflows" / "backend-delivery.yml").is_file()

    assert not (ROOT / "docker-compose.test.yml").exists()
    assert not (ROOT / "scripts" / "test_release.py").exists()
