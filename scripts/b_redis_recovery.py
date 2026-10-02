#!/usr/bin/env python3
"""Back up the B-owned Redis RDB and verify an isolated, disposable restore.

This is only an on-host Redis recovery drill. It does not back up MinIO, keep
an off-host copy, or authorize a B release.
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
from scripts.b_postgres_recovery import ROOT, _validate_backup_mount, validate_root  # noqa: E402

REDIS_IMAGE = "redis:7.4-alpine@sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499"
CONTAINER_ID = re.compile(r"[0-9a-f]{12,64}")


def _run(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def b_redis_id() -> str:
    result = subprocess.run([
        "docker", "ps", "--filter", "label=com.docker.compose.project=momcozy-lab-backend-us-east-uat",
        "--filter", "label=com.docker.compose.service=redis", "--format", "{{.ID}}",
    ], capture_output=True, text=True, check=True)
    ids = result.stdout.splitlines()
    if len(ids) != 1 or not CONTAINER_ID.fullmatch(ids[0]):
        raise ValueError("expected exactly one B-owned Redis container")
    return ids[0]


def _redis_command(container: str, *args: str) -> str:
    # Credentials are already inside the B Redis container's private process
    # environment. Never send a password through host argv, stdout or logs.
    return _run([
        "docker", "exec", container, "sh", "-ec",
        'REDISCLI_AUTH="$MOMCOZY_REDIS_ADMIN_PASSWORD" exec redis-cli --user deployment-admin --no-auth-warning --raw "$@"',
        "sh", *args,
    ])


def _source_key_count(container: str) -> int:
    count = _redis_command(container, "DBSIZE")
    if not re.fullmatch(r"[0-9]+", count):
        raise ValueError("B Redis DB 0 key count is invalid")
    return int(count)


def _snapshot(container: str, destination: Path) -> None:
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise ValueError("B Redis snapshot destination is unsafe")
    baseline = _redis_command(container, "LASTSAVE")
    if not re.fullmatch(r"[0-9]+", baseline):
        raise ValueError("B Redis LASTSAVE is invalid")
    # LASTSAVE has second precision: ensure the new snapshot can advance it.
    time.sleep(1.1)
    if _redis_command(container, "BGSAVE") != "Background saving started":
        raise ValueError("B Redis did not start a fresh RDB snapshot")
    for _ in range(90):
        info = _redis_command(container, "INFO", "persistence")
        fields = dict(line.strip().split(":", 1) for line in info.splitlines() if ":" in line)
        if fields.get("rdb_bgsave_in_progress") == "0" and fields.get("rdb_last_bgsave_status") != "ok":
            raise ValueError("B Redis RDB snapshot failed")
        if (fields.get("rdb_bgsave_in_progress") == "0"
                and fields.get("rdb_last_bgsave_status") == "ok"
                and int(_redis_command(container, "LASTSAVE")) > int(baseline)):
            break
        time.sleep(1)
    else:
        raise ValueError("B Redis did not complete a fresh RDB snapshot")
    # Stream through a private descriptor: docker cp cannot reliably copy a
    # Redis-generated file from a container tmpfs on every Docker backend.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            subprocess.run(["docker", "exec", container, "cat", "/data/dump.rdb"],
                           stdout=output, stderr=subprocess.PIPE, check=True)
        if destination.stat().st_size == 0:
            raise ValueError("B Redis RDB snapshot is empty")
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def _drill(dump: Path, expected_count: int) -> int:
    if dump.is_symlink() or not dump.is_file() or dump.stat().st_size == 0 or expected_count < 0:
        raise ValueError("B Redis recovery requires a nonempty private RDB")
    name = f"momcozy-b-redis-restore-{os.getpid()}"
    # No host port, B network or Docker volume. The image has a /data VOLUME,
    # explicitly replaced here by tmpfs. Disable AOF to load this exact RDB.
    _run([
        "docker", "run", "-d", "--rm", "--name", name, "--network", "none",
        "--read-only", "--tmpfs", "/data:uid=999,gid=1000,mode=0700",
        "-v", f"{dump}:/run/backup.dump:ro", "--entrypoint", "/bin/sh", REDIS_IMAGE,
        "-ec", 'redis-check-rdb /run/backup.dump >/dev/null && '
        'cp /run/backup.dump /data/dump.rdb && chown redis:redis /data/dump.rdb && '
        'exec /usr/bin/setpriv --reuid redis --regid redis --clear-groups '
        'redis-server --appendonly no --save "" --dir /data',
    ])
    try:
        for _ in range(30):
            ready = subprocess.run(["docker", "exec", name, "redis-cli", "ping"], capture_output=True, text=True, check=False)
            if ready.returncode == 0 and ready.stdout.strip() == "PONG":
                break
            time.sleep(1)
        else:
            raise ValueError("isolated B Redis recovery did not start")
        restored = _run(["docker", "exec", name, "redis-cli", "--raw", "DBSIZE"])
        if not re.fullmatch(r"[0-9]+", restored) or int(restored) != expected_count:
            raise ValueError("isolated B Redis recovery key count differs from source")
        return int(restored)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_locked(root: Path) -> Path:
    parent = _validate_backup_mount(root)
    container = b_redis_id()
    folder = parent / "redis"
    if folder.is_symlink() or (folder.exists() and (not folder.is_dir() or stat.S_IMODE(folder.stat().st_mode) != 0o700)):
        raise ValueError("B Redis backup directory is unsafe")
    folder.mkdir(mode=0o700, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-"), dir=folder))
    os.chmod(run, 0o700)
    before = _source_key_count(container)
    snapshot = run / "dump.rdb"
    _snapshot(container, snapshot)
    after = _source_key_count(container)
    if before != after:
        raise ValueError("B Redis changed key count during snapshot; schedule a quiet backup")
    restored = _drill(snapshot, before)
    if restored != before or _source_key_count(container) != before:
        raise ValueError("B Redis changed key count during recovery drill")
    manifest = run / "recovery-verified.json"
    descriptor = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump({"target": "north-america-staging", "verification": "isolated-redis-rdb-restore",
                   "rdb_file": snapshot.name, "sha256": _sha256(snapshot), "key_count": restored}, output, sort_keys=True)
    return run


def backup_and_drill(root: Path) -> Path:
    validate_root(root)
    lock = root / "shared" / "north-america-staging-release.lock"
    if lock.is_symlink() or not lock.parent.is_dir() or lock.parent.is_symlink() or stat.S_IMODE(lock.parent.stat().st_mode) != 0o700:
        raise ValueError("B release lock directory is unsafe")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Back up B Redis and verify isolated RDB recovery")
    args = parser.parse_args(argv)
    if not args.apply:
        print("No changes made. --apply performs B Redis backup and isolated recovery on the approved host.")
        return 0
    try:
        require_local_docker()
        backup_and_drill(ROOT)
    except (OSError, ValueError, subprocess.SubprocessError):
        print("FAIL B Redis backup/recovery: incomplete; deployment remains blocked", file=sys.stderr)
        return 1
    print("B Redis backup and isolated recovery passed; verify the other on-host recovery gates separately.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
