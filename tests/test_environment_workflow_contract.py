from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_deployable_environment_files_are_canonical() -> None:
    for path in (
        ROOT / "env" / "local.env.example",
        ROOT / "env" / "staging.env.example",
        ROOT / "env" / "production.env.example",
        ROOT / "docker-compose.local.yml",
        ROOT / "docker-compose.deploy.yml",
        ROOT / "scripts" / "release.py",
        ROOT / ".github" / "workflows" / "backend-delivery.yml",
    ):
        assert path.is_file(), path

    for path in (
        ROOT / "env" / "compose.local.env.example",
        ROOT / "env" / "compose.test.env.example",
        ROOT / "env" / "account-auth.env.example",
        ROOT / "docker-compose.test.yml",
        ROOT / "scripts" / "test_release.py",
        ROOT / ".github" / "workflows" / "backend-test-delivery.yml",
    ):
        assert not path.exists(), path


def test_deploy_profile_is_environment_neutral() -> None:
    compose = (ROOT / "docker-compose.deploy.yml").read_text()
    release = (ROOT / "scripts" / "release.py").read_text()
    workflow = (ROOT / ".github" / "workflows" / "backend-delivery.yml").read_text()

    assert "MOMCOZY_TEST_" not in compose + release + workflow
    assert "docker-compose.test.yml" not in compose + release + workflow
    assert '"environment": spec.environment' in release
    assert "choices:" not in workflow  # GitHub uses `options`, not an ad-hoc list.
    assert "options:\n          - staging\n          - production" in workflow
    assert "environment:\n      name: ${{ inputs.environment }}" in workflow


def test_environment_templates_have_one_semantic_identity() -> None:
    expected = {
        "local": "local",
        "staging": "staging",
        "production": "production",
    }
    for filename, environment in expected.items():
        text = (ROOT / "env" / f"{filename}.env.example").read_text()
        assert f"APP_ENV={environment}" in text
        assert "MOMCOZY_TEST_" not in text
