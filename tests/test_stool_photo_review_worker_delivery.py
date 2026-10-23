"""B-lane cleanup must never silently enable third-party child-image review."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_b_compose_has_fail_closed_review_and_cleanup_worker():
    compose = (ROOT / "docker-compose.us-east-uat.yml").read_text()
    block = compose.split("  stool-photo-review-worker:", 1)[1].split("\n  postgres:", 1)[0]
    assert "python -m app.workers.stool_photo_reviews" in block
    assert "STOOL_PHOTO_REVIEW_PROVIDER" not in block
    assert "STOOL_PHOTO_THIRD_PARTY_APPROVED" not in block
    assert "postgres:" in block and "minio:" in block
    assert "restart: unless-stopped" in block


def test_b_compose_delivers_the_analysis_worker_and_isolated_specialty_service():
    compose = (ROOT / "docker-compose.us-east-uat.yml").read_text()
    assert "  stool-analysis-worker:" in compose
    assert "python -m app.workers.stool_analyses" in compose
    specialty = compose.split("  stool-specialty:", 1)[1].split("\n  postgres:", 1)[0]
    assert "services.stool_specialty.main:app" in specialty
    assert "env_file:" not in specialty
    for secret in ("DATABASE_URL", "REDIS_URL", "OBJECT_STORAGE_ACCESS_KEY_ID", "OBJECT_STORAGE_SECRET_ACCESS_KEY"):
        assert secret not in specialty


def test_b_activation_and_migration_account_for_cleanup():
    sources = (ROOT / "scripts/b_activate_first.py").read_text() + (ROOT / "scripts/b_migrate_first.py").read_text()
    assert "stool-photo-review-worker" in sources
