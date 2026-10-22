from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_delivery_contract_uses_canonical_environment_names() -> None:
    assert (ROOT / "docker-compose.deploy.yml").is_file()
    assert (ROOT / "env" / "staging.env.example").is_file()
    assert not (ROOT / "env" / "production.env.example").exists()
    assert (ROOT / "scripts" / "release.py").is_file()
    assert (ROOT / ".github" / "workflows" / "backend-singapore-delivery.yml").is_file()

    assert not (ROOT / "docker-compose.test.yml").exists()
    assert not (ROOT / "scripts" / "test_release.py").exists()


def test_release_builds_only_use_main() -> None:
    import json

    for workflow in ("backend-ci.yml", "backend-north-america-validation.yml"):
        text = (ROOT / ".github/workflows" / workflow).read_text()
        assert "pull_request:" not in text
        assert "main" in text
        assert "branches: [dev]" not in text
    source = json.loads((ROOT / "deploy/us-east-uat/release-source.json").read_text())
    assert source["source_branch"] == "main"
    assert not (ROOT / "env/production.env.example").exists()


def test_pull_requests_validate_without_publishing_or_deploying() -> None:
    workflow = (ROOT / ".github/workflows/backend-pr-verify.yml").read_text()
    assert "pull_request:\n    branches: [main]" in workflow
    assert "permissions:\n  contents: read" in workflow
    for check in ("ruff check", "mypy app scripts", "pytest tests", "alembic", "export_openapi.py"):
        assert check in workflow
    for forbidden in ("docker build", "docker push", "packages: write", "workflow_dispatch:", "secrets."):
        assert forbidden not in workflow
