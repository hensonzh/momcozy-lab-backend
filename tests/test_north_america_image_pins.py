"""North America self-hosted stateful images must not drift under mutable public tags."""

from pathlib import Path

from scripts import north_america_postgres_recovery, north_america_redis_recovery

ROOT = Path(__file__).resolve().parents[1]


def test_north_america_stateful_services_and_restore_use_exact_images() -> None:
    compose = (ROOT / "docker-compose.us-east-uat.yml").read_text()
    for image in (north_america_postgres_recovery.POSTGRES_IMAGE, north_america_redis_recovery.REDIS_IMAGE):
        assert "@sha256:" in image
        assert f"image: {image}" in compose
        assert image in (ROOT / "scripts/check_north_america_infra_contract.sh").read_text()
    assert "postgres:16\n" not in compose
    assert "redis:7.4-alpine\n" not in compose


def test_north_america_minio_build_pins_its_base_without_touching_singapore() -> None:
    b = (ROOT / "deploy/us-east-uat/Minio.Dockerfile").read_text()
    for prefix in ("FROM golang:1.24.8-alpine@sha256:", "FROM alpine:3.21@sha256:"):
        assert prefix in b
    workflow = (ROOT / ".github/workflows/backend-north-america-validation.yml").read_text()
    assert "docker build --platform linux/amd64 -f deploy/us-east-uat/Minio.Dockerfile" in workflow
    assert "docker build -f deploy/shared/Minio.Dockerfile" not in workflow
    assert (ROOT / "deploy/shared/Minio.Dockerfile").read_text().startswith("# Reproducible MinIO")
