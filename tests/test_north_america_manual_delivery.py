"""Manual North America delivery is a protected, main-only host operation."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/backend-north-america-delivery.yml"


def test_manual_delivery_is_separate_from_image_publication() -> None:
    text = WORKFLOW.read_text()
    assert "workflow_dispatch:" in text
    assert "push:" not in text
    assert "pull_request:" not in text
    assert "github.ref == 'refs/heads/main'" in text
    assert "name: north-america-staging" in text
    assert "cancel-in-progress: false" in text
    assert "NORTH_AMERICA_APPROVERS" in text
    assert "GITHUB_TRIGGERING_ACTOR" in text
    assert "StrictHostKeyChecking=no" not in text
    assert "NORTH_AMERICA_SSH_KNOWN_HOSTS" in text
    driver = (ROOT / "scripts/north_america_manual_delivery.py").read_text()
    assert "north-america-staging-release.lock" in driver
    assert "--preflight" in text and "--apply" in text
    assert "north_america_first_release.py" in driver
    assert "north_america_update_release.py" in driver
    assert "--confirm-client-compatible" in text
    assert "first-release" in text and "update" in text and "rollback" in text


def test_image_and_source_pairs_are_proven_before_remote_apply() -> None:
    text = WORKFLOW.read_text()
    assert "refs/heads/main" in text
    assert "north-america-validation.yml" in text
    assert 'core_workflow="$service-ci.yml"' in text
    assert 'No successful $service core CI for approved commit' in text
    assert "for service in backend agent" in text
    assert "north-america-main-" in text
    assert "gh run download" in text
    assert "ci_digest" in text and '[[ "$digest" == "$ci_digest" ]]' in text
    # CI checks out this repository alone; Agent verifies its own workflow.
    published = (ROOT / '.github/workflows/backend-north-america-validation.yml').read_text()
    assert 'actions/upload-artifact@v4' in published
    assert 'north-america-image-digest.txt' in published
    assert "sha256:" in text
    assert "north-america-staging" in text
    assert "--backend-commit" in text and "--agent-commit" in text
    driver = (ROOT / "scripts/north_america_manual_delivery.py").read_text()
    assert "--{service}-image" in driver
    assert "--{service}-image-id" in driver
    assert "preflight" in driver and "subprocess.run(preflight, check=True)" in driver
    assert "docker compose up" not in text


def test_no_old_recovery_script_names_remain_in_mutating_runners() -> None:
    for name in ("north_america_first_release.py", "north_america_update_release.py"):
        source = (ROOT / "scripts" / name).read_text()
        assert 'f"north_america_{name}_recovery.py"' in source or 'f"north_america_{kind}_recovery.py"' in source
        assert 'f"b_' not in source
