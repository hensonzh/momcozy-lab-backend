"""B MinIO synthetic restore must never touch live Compose volumes or A."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_b_minio_recovery_contract.sh"


def test_synthetic_minio_recovery_is_isolated() -> None:
    text = SCRIPT.read_text()
    assert "momcozy-us-east-uat-minio:9e49d5e7a648f00e" in text
    assert "org.opencontainers.image.revision" in text
    assert "--network none" in text
    assert "--tmpfs /data" in text
    assert "--rm" in text
    assert ":/run/backup:ro" in text
    assert "momcozy-product-us-east-uat" in text
    assert "momcozy-agent-us-east-uat" in text
    assert "mc mirror" in text
    assert "sha256sum" in text
    assert "trap cleanup EXIT" in text
    assert "stat.S_IMODE" in text  # consistent 0700 check on Linux CI and macOS
    assert "stat -f %Lp" not in text  # GNU stat writes a filesystem report before failing
    # Archiving '.' restores the source directory's mode onto the 0700 backup root on Linux.
    assert "tar -C /tmp/momcozy-b-backup -cf - product agent" in text
    assert "tar -C /tmp/momcozy-b-backup -cf - ." not in text
    for forbidden in ("docker compose", "docker volume", "--publish", "--privileged", "mc mirror --remove", "/opt/momcozy-lab-staging"):
        assert forbidden not in text
