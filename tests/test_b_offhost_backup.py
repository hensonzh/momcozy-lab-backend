"""Off-host copy must be built from verified B-only recovery artifacts."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts import b_offhost_backup as backup


def _artifact(root: Path, kind: str, name: str, content: bytes) -> Path:
    run = root / kind / "run"
    run.mkdir(parents=True, exist_ok=True)
    file = run / name
    file.write_bytes(content)
    return file


def _valid_runs(root: Path) -> None:
    product = _artifact(root, "postgres", "momcozy_lab_backend_uat.dump", b"pg")
    agent = _artifact(root, "postgres", "momcozy_lab_agent_uat.dump", b"agent")
    (root / "postgres/run/recovery-verified.json").write_text(json.dumps({
        "target": "north-america-staging", "verification": "isolated-postgres-restore", "databases": {
            "momcozy_lab_backend_uat": {"file": product.name, "sha256": hashlib.sha256(b"pg").hexdigest(), "alembic_revision": "rev"},
            "momcozy_lab_agent_uat": {"file": agent.name, "sha256": hashlib.sha256(b"agent").hexdigest(), "alembic_revision": "rev"},
        },
    }))
    _artifact(root, "redis", "dump.rdb", b"rdb")
    (root / "redis/run/recovery-verified.json").write_text(json.dumps({
        "target": "north-america-staging", "verification": "isolated-redis-rdb-restore", "rdb_file": "dump.rdb",
        "sha256": hashlib.sha256(b"rdb").hexdigest(), "key_count": 0,
    }))
    _artifact(root, "minio", "iam.zip", b"iam")
    (root / "minio/run/recovery-verified.json").write_text(json.dumps({
        "target": "north-america-staging", "verification": "isolated-minio-two-bucket-iam-restore",
        "iam_sha256": hashlib.sha256(b"iam").hexdigest(), "objects": {"product": {}, "agent": {}},
    }))
    (root / "minio/run/product").mkdir()
    (root / "minio/run/agent").mkdir()
    for kind in ("postgres", "redis", "minio"):
        (root / kind / "run").chmod(0o700)


def test_select_requires_all_three_live_manifests(tmp_path: Path) -> None:
    _valid_runs(tmp_path)
    (tmp_path / "minio/run/recovery-verified.json").unlink()
    with pytest.raises(ValueError, match="minio"):
        backup.select_latest(tmp_path)


def test_select_rejects_symlinked_artifact(tmp_path: Path) -> None:
    _valid_runs(tmp_path)
    marker = tmp_path / "redis/run/dump.rdb"
    marker.unlink()
    marker.symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symlink"):
        backup.select_latest(tmp_path)


def test_select_verifies_recovery_artifact_hashes(tmp_path: Path) -> None:
    _valid_runs(tmp_path)
    (tmp_path / "postgres/run/momcozy_lab_backend_uat.dump").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        backup.select_latest(tmp_path)


def test_package_three_verified_runs(tmp_path: Path) -> None:
    import tarfile
    _valid_runs(tmp_path)
    output = tmp_path / "copy.tar.gz"
    backup.package(tmp_path, output)
    assert output.stat().st_mode & 0o777 == 0o600
    with tarfile.open(output) as archive:
        names = archive.getnames()
    assert len(names) == 7
    assert any(name.startswith("postgres/run/") for name in names)
    assert any(name.startswith("redis/run/") for name in names)
    assert any(name.startswith("minio/run/") for name in names)


def test_direct_script_invocation() -> None:
    import subprocess
    result = subprocess.run(["python3", str(Path(__file__).resolve().parents[1] / "scripts/b_offhost_backup.py"), "--help"],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0
