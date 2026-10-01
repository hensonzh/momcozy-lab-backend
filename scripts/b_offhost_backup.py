#!/usr/bin/env python3
"""Package checksum-verified live B recovery evidence for encrypted off-host retention.

Only selects completed PostgreSQL/Redis/MinIO recovery runs under the B backup
mount. It does not transmit the archive or imply off-host recovery succeeded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tarfile
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from scripts.b_postgres_recovery import ROOT, _validate_backup_mount, validate_root  # noqa: E402

EXPECTED = {"postgres": "isolated-postgres-restore", "redis": "isolated-redis-rdb-restore",
            "minio": "isolated-minio-two-bucket-iam-restore"}
DATABASES = ("momcozy_lab_backend_uat", "momcozy_lab_agent_uat")
BUCKETS = ("product", "agent")


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("B backup contains a symlink")
    if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("B backup artifact is not a regular file")


def _safe_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+", name)) and name not in (".", "..")


def _check_hash(path: Path, digest: str) -> None:
    _regular(path)
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or _hash(path) != digest:
        raise ValueError("B backup artifact checksum mismatch")


def _validate_run(kind: str, run: Path) -> None:
    if run.is_symlink() or not run.is_dir() or stat.S_IMODE(run.stat().st_mode) != 0o700:
        raise ValueError("B backup run is not private")
    manifest = run / "recovery-verified.json"
    _regular(manifest)
    data = json.loads(manifest.read_text())
    if (not isinstance(data, dict) or data.get("target") != "north-america-staging"
            or data.get("verification") != EXPECTED[kind]):
        raise ValueError("B recovery manifest identity differs")
    if kind == "postgres":
        databases = data.get("databases")
        if not isinstance(databases, dict) or set(databases) != set(DATABASES):
            raise ValueError("B PostgreSQL backup databases missing")
        for database in DATABASES:
            record = databases[database]
            filename = record.get("file", "")
            if filename != f"{database}.dump" or not _safe_name(filename) or not record.get("alembic_revision"):
                raise ValueError("B PostgreSQL recovery evidence is incomplete")
            _check_hash(run / filename, record.get("sha256"))
    elif kind == "redis":
        if data.get("rdb_file") != "dump.rdb" or not isinstance(data.get("key_count"), int):
            raise ValueError("B Redis recovery evidence is incomplete")
        _check_hash(run / "dump.rdb", data.get("sha256"))
    else:
        _check_hash(run / "iam.zip", data.get("iam_sha256"))
        objects = data.get("objects")
        if not isinstance(objects, dict) or set(objects) != set(BUCKETS):
            raise ValueError("B MinIO recovery evidence is incomplete")
        for bucket, entries in objects.items():
            root = run / bucket
            if root.is_symlink() or not root.is_dir() or not isinstance(entries, dict):
                raise ValueError("B MinIO backup bucket missing")
            seen = set()
            for directory, subdirs, files in os.walk(root, followlinks=False):
                if any((Path(directory) / entry).is_symlink() for entry in (*subdirs, *files)):
                    raise ValueError("B backup contains a symlink")
                for filename in files:
                    path = Path(directory) / filename
                    name = path.relative_to(root).as_posix()
                    if name not in entries:
                        raise ValueError("B MinIO backup has an unlisted object")
                    _check_hash(path, entries[name])
                    seen.add(name)
            if seen != set(entries):
                raise ValueError("B MinIO backup objects missing")


def select_latest(root: Path) -> dict[str, Path]:
    chosen = {}
    for kind in EXPECTED:
        parent = root / kind
        if parent.is_symlink() or not parent.is_dir():
            raise ValueError(f"B {kind} recovery directory missing")
        candidates = sorted((path for path in parent.iterdir() if path.is_dir() and not path.is_symlink()
                             and (path / "recovery-verified.json").is_file()), reverse=True)
        if not candidates:
            raise ValueError(f"B {kind} recovery manifest missing")
        run = candidates[0]
        _validate_run(kind, run)
        chosen[kind] = run
    return chosen


def package(backup_root: Path, output: Path) -> None:
    if output.exists() or output.is_symlink() or not output.is_absolute():
        raise ValueError("B off-host plaintext archive path is unsafe")
    runs = select_latest(backup_root)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream, tarfile.open(fileobj=stream, mode="w:gz") as archive:
            for kind, run in runs.items():
                for directory, subdirs, files in os.walk(run, followlinks=False):
                    if any((Path(directory) / entry).is_symlink() for entry in (*subdirs, *files)):
                        raise ValueError("B backup contains a symlink")
                    for filename in files:
                        path = Path(directory) / filename
                        _regular(path)
                        archive.add(path, arcname=f"{kind}/{run.name}/{path.relative_to(run)}", recursive=False)
    except Exception:
        output.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.apply:
        print("No B backup packaged; --apply required.")
        return 0
    try:
        validate_root(ROOT)
        root = _validate_backup_mount(ROOT)
        package(root, args.output)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, tarfile.TarError):
        print("FAIL B off-host package: recovery evidence invalid; no delivery completed", file=sys.stderr)
        return 1
    print("B recovery artifacts packaged privately; still requires encryption, off-host receipt and restore.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
