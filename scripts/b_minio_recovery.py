#!/usr/bin/env python3
"""Back up B's two live MinIO buckets and IAM, then restore on an isolated server.

Private on-host recovery evidence is not off-host retention. Never touch A, remove
live objects, or regard this drill alone as an application release approval.
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
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from scripts.b_postgres_recovery import ROOT, _validate_backup_mount, validate_root  # noqa: E402

IMAGE = "momcozy-us-east-uat-minio:9e49d5e7a648f00e"
REVISION = "9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a"
BUCKETS = {"product": "momcozy-product-us-east-uat", "agent": "momcozy-agent-us-east-uat"}
CONTAINER_ID = re.compile(r"[0-9a-f]{12,64}")


def _run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, **kwargs)


def b_minio_id() -> str:
    result = _run([
        "docker", "ps", "--filter", "label=com.docker.compose.project=momcozy-lab-backend-us-east-uat",
        "--filter", "label=com.docker.compose.service=minio", "--format", "{{.ID}}",
    ])
    ids = result.stdout.decode().splitlines()
    if len(ids) != 1 or not CONTAINER_ID.fullmatch(ids[0]):
        raise ValueError("expected exactly one B-owned MinIO container")
    return ids[0]


def _digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def _files(folder: Path) -> dict[str, str]:
    result = {}
    for root, directories, files in os.walk(folder, followlinks=False):
        if any((Path(root) / name).is_symlink() for name in (*directories, *files)):
            raise ValueError("B MinIO backup contains a symlink")
        for name in files:
            path = Path(root) / name
            if not path.is_file():
                raise ValueError("B MinIO backup contains a non-file")
            result[path.relative_to(folder).as_posix()] = _digest(path)
    return result


def _extract(stream: object, folder: Path) -> None:
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            if (member.name not in {"product", "agent", "iam.zip"}
                    and not any(member.name.startswith(prefix + "/") for prefix in ("product", "agent"))):
                raise ValueError("unexpected B MinIO archive entry")
            if not (member.isfile() or member.isdir()) or member.issym() or member.islnk():
                raise ValueError("unsafe B MinIO archive entry")
            target = folder / member.name
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
            else:
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with archive.extractfile(member) as source, target.open("xb") as output:
                    if source is None:
                        raise ValueError("empty B MinIO archive entry")
                    while data := source.read(1024 * 1024):
                        output.write(data)
                target.chmod(0o600)


def _capture(container: str, folder: Path) -> None:
    stage = f"/tmp/momcozy-b-live-backup-{os.getpid()}"
    script = '''set -eu
      export MC_CONFIG_DIR="$1/config"
      export MC_HOST_b="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@127.0.0.1:9000"
      mkdir -m 0700 -p "$1/product" "$1/agent"
      mc mirror b/momcozy-product-us-east-uat "$1/product" >/dev/null
      mc mirror b/momcozy-agent-us-east-uat "$1/agent" >/dev/null
      mc admin cluster iam export b --output "$1/iam.zip" >/dev/null
      test -s "$1/iam.zip"
    '''
    try:
        _run(["docker", "exec", container, "sh", "-ec", script, "sh", stage])
        with subprocess.Popen(
            ["docker", "exec", container, "tar", "-C", stage, "-cf", "-", "product", "agent", "iam.zip"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ) as process:
            if process.stdout is None:
                raise ValueError("B MinIO archive stream unavailable")
            _extract(process.stdout, folder)
            if process.wait() != 0:
                raise ValueError("B MinIO archive failed")
    finally:
        subprocess.run(["docker", "exec", container, "rm", "-rf", "--", stage], capture_output=True, check=False)
    if not (folder / "iam.zip").is_file() or (folder / "iam.zip").stat().st_size == 0:
        raise ValueError("B MinIO IAM export missing")
    for name in BUCKETS:
        if not (folder / name).is_dir():
            raise ValueError("B MinIO bucket export missing")


def _drill(folder: Path) -> None:
    name = f"momcozy-b-minio-restore-{os.getpid()}"
    _run(["docker", "run", "-d", "--rm", "--name", name, "--network", "none",
          "--tmpfs", "/data:mode=0700", "-v", f"{folder}:/run/backup:ro",
          "-e", "MINIO_ROOT_USER=synthetic_b_root", "-e", "MINIO_ROOT_PASSWORD=synthetic_b_restore_only",
          IMAGE, "server", "/data", "--address", ":9000"])
    try:
        for _ in range(40):
            ready = subprocess.run(["docker", "exec", name, "curl", "-fsS", "http://127.0.0.1:9000/minio/health/ready"],
                                   capture_output=True, check=False)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise ValueError("isolated B MinIO server did not become ready")
        script = '''set -eu
          export MC_CONFIG_DIR=/tmp/momcozy-b-mc
          export MC_HOST_b="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@127.0.0.1:9000"
          mc mb b/momcozy-product-us-east-uat b/momcozy-agent-us-east-uat >/dev/null
          mc mirror /run/backup/product b/momcozy-product-us-east-uat >/dev/null
          mc mirror /run/backup/agent b/momcozy-agent-us-east-uat >/dev/null
          mc admin cluster iam import b /run/backup/iam.zip >/dev/null
          mkdir -m 0700 -p /tmp/verified/product /tmp/verified/agent
          mc mirror b/momcozy-product-us-east-uat /tmp/verified/product >/dev/null
          mc mirror b/momcozy-agent-us-east-uat /tmp/verified/agent >/dev/null
          tar -C /tmp/verified -cf - product agent
        '''
        with subprocess.Popen(["docker", "exec", name, "sh", "-ec", script], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) as process:
            if process.stdout is None:
                raise ValueError("isolated B MinIO restore stream unavailable")
            with tempfile.TemporaryDirectory(prefix="momcozy-b-restore-") as tmp:
                restored = Path(tmp)
                _extract(process.stdout, restored)
                if process.wait() != 0:
                    raise ValueError("isolated B MinIO restore failed")
                for kind in BUCKETS:
                    if _files(folder / kind) != _files(restored / kind):
                        raise ValueError("isolated B MinIO bucket contents differ")
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)


def _backup_locked(root: Path) -> Path:
    parent = _validate_backup_mount(root)
    container = b_minio_id()
    image_revision = _run(["docker", "image", "inspect", IMAGE, "--format", '{{index .Config.Labels "org.opencontainers.image.revision"}}']).stdout.decode().strip()
    if image_revision != REVISION:
        raise ValueError("B MinIO image revision differs")
    target = parent / "minio"
    if target.is_symlink() or (target.exists() and (not target.is_dir() or stat.S_IMODE(target.stat().st_mode) != 0o700)):
        raise ValueError("B MinIO backup root is unsafe")
    target.mkdir(mode=0o700, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix=datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-"), dir=target))
    os.chmod(folder, 0o700)
    _capture(container, folder)
    before = {kind: _files(folder / kind) for kind in BUCKETS}
    _drill(folder)
    # A changing source is not a consistent backup; recheck live by recapturing
    # into an unrelated private scratch folder, without modifying the original.
    with tempfile.TemporaryDirectory(prefix="momcozy-b-recheck-") as tmp:
        snapshot = Path(tmp)
        _capture(container, snapshot)
        if any(before[kind] != _files(snapshot / kind) for kind in BUCKETS):
            raise ValueError("live B MinIO objects changed during recovery drill")
    manifest = folder / "recovery-verified.json"
    with manifest.open("x") as output:
        json.dump({"target": "north-america-staging", "verification": "isolated-minio-two-bucket-iam-restore",
                   "iam_sha256": _digest(folder / "iam.zip"), "objects": before}, output, sort_keys=True)
    manifest.chmod(0o600)
    return folder


def backup_and_drill(root: Path) -> Path:
    validate_root(root)
    lock = root / "shared/north-america-staging-release.lock"
    if lock.is_symlink() or not lock.parent.is_dir() or stat.S_IMODE(lock.parent.stat().st_mode) != 0o700:
        raise ValueError("B release lock is unsafe")
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
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if not args.apply:
        print("No B MinIO backup made; --apply is required.")
        return 0
    try:
        backup_and_drill(ROOT)
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError):
        print("FAIL B MinIO backup/recovery: incomplete; state untouched", file=sys.stderr)
        return 1
    print("B MinIO two-bucket and IAM backup/isolated recovery passed; off-host retention remains pending.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
