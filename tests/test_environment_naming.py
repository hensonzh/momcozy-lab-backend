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


def test_main_push_ci_remains_without_pr_workflow() -> None:
    assert not (ROOT / ".github/workflows/backend-pr-verify.yml").exists()
    workflow = (ROOT / ".github/workflows/backend-ci.yml").read_text()
    assert "push:" in workflow and "main" in workflow
    assert "pull_request:" not in workflow
