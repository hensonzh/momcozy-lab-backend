"""B-lane cleanup must never silently enable third-party child-image review."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_b_compose_has_fail_closed_review_and_cleanup_worker():
    compose = (ROOT / "docker-compose.us-east-uat.yml").read_text()
    block = compose.split("  stool-photo-review-worker:", 1)[1].split("\n  postgres:", 1)[0]
    assert "python -m app.workers.stool_photo_reviews" in block
    assert 'STOOL_PHOTO_REVIEW_PROVIDER: "disabled"' in compose
    assert 'STOOL_PHOTO_THIRD_PARTY_APPROVED: "false"' in compose
    assert "postgres:" in block and "minio:" in block
    assert "restart: unless-stopped" in block


def test_b_activation_and_migration_account_for_cleanup():
    sources = (ROOT / "scripts/north_america_activate_first.py").read_text() + (ROOT / "scripts/north_america_migrate_first.py").read_text()
    assert "stool-photo-review-worker" in sources


def test_ci_review_worker_receives_ephemeral_auth_key_for_startup():
    compose = (ROOT / "docker-compose.ci.yml").read_text()
    worker = compose.split("  stool-photo-review-worker:", 1)[1].split("\n  migrate:", 1)[0]
    assert "AUTH_JWT_PRIVATE_KEY_B64: ${AUTH_JWT_PRIVATE_KEY_B64:?Set AUTH_JWT_PRIVATE_KEY_B64}" in worker
    assert "STOOL_PHOTO_REVIEW_PROVIDER: disabled" in worker
