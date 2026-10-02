#!/usr/bin/env python3
"""Back up B PostgreSQL databases and verify both on isolated, disposable servers.

This covers PostgreSQL only. It is NOT sufficient to enable B deployment:
MinIO buckets and Redis also require real backup/isolated recovery.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
import tempfile
from datetime import UTC, datetime
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from scripts.b_docker_context import require_local_docker  # noqa: E402

ROOT = Path("/opt/momcozy-lab-us-east-uat")
DATABASES = ("momcozy_lab_backend_uat", "momcozy_lab_agent_uat")
POSTGRES_IMAGE = "postgres:16@sha256:1a6ab3f5345eb6dbe04a1349529caabdb0ab09293a09590fad07b2246bfa4b54"
BACKUP_SOURCE = Path("/data/momcozy-lab-us-east-uat/backups")


def validate_root(root: Path) -> None:
    if root != ROOT or root.is_symlink() or not root.is_dir() or stat.S_IMODE(root.stat().st_mode) & 0o077:
        raise ValueError("B backup requires the private isolated host root")


def b_postgres_id() -> str:
    result = subprocess.run([
        "docker", "ps", "--filter", "label=com.docker.compose.project=momcozy-lab-backend-us-east-uat",
        "--filter", "label=com.docker.compose.service=postgres", "--format", "{{.ID}}",
    ], capture_output=True, text=True, check=True)
    ids = result.stdout.splitlines()
    if len(ids) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", ids[0]):
        raise ValueError("expected exactly one B-owned PostgreSQL container")
    return ids[0]


def _run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _dump(container: str, database: str, destination: Path) -> None:
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            subprocess.run(
                ["docker", "exec", container, "sh", "-ec",
                 'exec pg_dump -Fc --username "$POSTGRES_USER" --dbname "$1"', "sh", database],
                stdout=output, stderr=subprocess.PIPE, check=True,
            )
        if destination.stat().st_size == 0:
            raise ValueError("B PostgreSQL dump is empty")
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _drill(database: str, dump: Path, expected_revision: str) -> None:
    if database not in DATABASES or dump.is_symlink() or not dump.is_file() or dump.stat().st_size == 0:
        raise ValueError("B recovery dump is missing or unsafe")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", expected_revision):
        raise ValueError("B recovery revision is missing or unsafe")
    # No host ports, no named volume and no connection to the B network. Only
    # the just-created dump is bind-mounted read-only into a fresh scratch DB.
    # Do not log docker stderr: PostgreSQL error output can contain data.
    name = f"momcozy-b-pg-restore-{os.getpid()}-{database}"
    _run([
        "docker", "run", "-d", "--rm", "--name", name, "--network", "none", "--tmpfs", "/var/lib/postgresql/data:mode=0700",
        "-e", "POSTGRES_USER=synthetic_restore", "-e", "POSTGRES_PASSWORD=synthetic_restore_only",
        "-e", f"POSTGRES_DB={database}", "-v", f"{dump}:/run/backup.dump:ro", POSTGRES_IMAGE,
    ])
    try:
        for _ in range(30):
            ready = subprocess.run(["docker", "exec", name, "pg_isready", "-U", "synthetic_restore", "-d", database],
                                   capture_output=True, check=False)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise ValueError("isolated B PostgreSQL recovery did not start")
        _run(["docker", "exec", name, "pg_restore", "--exit-on-error", "--no-owner", "--no-acl",
              "--username", "synthetic_restore", "--dbname", database, "/run/backup.dump"])
        # Verify the actual recovered schema, not only an empty scratch DB.
        revision = _run(["docker", "exec", name, "psql", "-X", "-U", "synthetic_restore", "-d", database,
                         "--tuples-only", "--no-align", "--set", "ON_ERROR_STOP=1",
                         "-c", "SELECT version_num FROM public.alembic_version"])
        if revision != expected_revision:
            raise ValueError("isolated B PostgreSQL recovery revision differs from source")
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


def backup_and_drill(root: Path) -> Path:
    validate_root(root)
    lock = root / "shared" / "north-america-staging-release.lock"
    if lock.is_symlink() or not lock.parent.is_dir() or lock.parent.is_symlink() or stat.S_IMODE(lock.parent.stat().st_mode) != 0o700:
        raise ValueError("B release lock directory is missing or unsafe")
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
            raise ValueError("B release lock is not private")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("B release lock is held") from exc
        return _backup_locked(root)
    finally:
        os.close(descriptor)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_revision(container: str, database: str) -> str:
    revision = _run(["docker", "exec", container, "sh", "-ec",
                     'exec psql -X --username "$POSTGRES_USER" --dbname "$1" --tuples-only --no-align --set ON_ERROR_STOP=1 --command "SELECT version_num FROM public.alembic_version"',
                     "sh", database])
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", revision):
        raise ValueError("B source database has no safe Alembic revision")
    return revision


def _validate_backup_mount(root: Path) -> Path:
    parent = root / "backups"
    if (parent.is_symlink() or not parent.is_dir()
            or stat.S_IMODE(parent.stat().st_mode) != 0o700
            or not os.path.ismount(parent)):
        raise ValueError("B backup requires mounted B backup storage")
    data = Path("/data")
    if (not os.path.ismount(data) or BACKUP_SOURCE.is_symlink()
            or not BACKUP_SOURCE.is_dir()
            or not os.path.samefile(parent, BACKUP_SOURCE)
            or parent.stat().st_dev == root.stat().st_dev):
        raise ValueError("B backup storage must be the isolated /data mount")
    return parent


def _backup_locked(root: Path) -> Path:
    parent = _validate_backup_mount(root)
    container = b_postgres_id()
    backup_root = parent / "postgres"
    if backup_root.is_symlink() or (backup_root.exists() and (not backup_root.is_dir() or stat.S_IMODE(backup_root.stat().st_mode) != 0o700)):
        raise ValueError("B PostgreSQL backup directory is not private")
    backup_root.mkdir(mode=0o700, exist_ok=True)
    if backup_root.is_symlink() or stat.S_IMODE(backup_root.stat().st_mode) != 0o700:
        raise ValueError("B PostgreSQL backup directory is not private")
    run_dir = Path(tempfile.mkdtemp(prefix=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-"), dir=backup_root))
    os.chmod(run_dir, 0o700)
    records: dict[str, dict[str, str]] = {}
    for database in DATABASES:
        artifact = run_dir / f"{database}.dump"
        revision = _source_revision(container, database)
        _dump(container, database, artifact)
        _drill(database, artifact, revision)
        if _source_revision(container, database) != revision:
            raise ValueError("B source database revision changed during backup")
        records[database] = {"file": artifact.name, "sha256": _sha256(artifact), "alembic_revision": revision}
    manifest = run_dir / "recovery-verified.json"
    descriptor = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump({"target": "north-america-staging", "verification": "isolated-postgres-restore", "databases": records}, handle, sort_keys=True)
    return run_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Back up and restore B-only PostgreSQL on disposable volumes")
    args = parser.parse_args(argv)
    if not args.apply:
        print("No changes made. --apply performs B PostgreSQL backup and isolated recovery on the approved host.")
        return 0
    try:
        require_local_docker()
        backup_and_drill(ROOT)
    except (OSError, ValueError, subprocess.SubprocessError):
        print("FAIL B PostgreSQL backup/recovery: incomplete; deployment remains blocked", file=sys.stderr)
        return 1
    print("B PostgreSQL backup and isolated recovery passed for both databases; MinIO/Redis gates still pending.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
