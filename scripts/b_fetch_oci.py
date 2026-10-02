#!/usr/bin/env python3
"""Fetch a B GHCR image by immutable digest into a checked linux/amd64 OCI archive.

Read a repository-scoped pull token from a private file. This never stores
registry credentials in Docker's persistent configuration or command arguments.
The archive can be imported only after its source digest and blobs are checked.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import sys
import tarfile
import tempfile
import urllib.request
from urllib.parse import urlencode
from pathlib import Path

ACCEPT = ",".join((
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
))
REPOSITORIES = ("momcozy-lab-backend", "momcozy-lab-agent")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
PRIVATE_ROOT = Path("/dev/shm/momcozy-b-ghcr-transfer")


def validate(repo: str, digest: str, output: Path) -> None:
    if repo not in REPOSITORIES or not DIGEST.fullmatch(digest):
        raise ValueError("unapproved B repository or image digest")
    if output.exists() or output.is_symlink():
        raise ValueError("OCI output already exists")


def registry_token(repo: str, pat: str, *, username: str) -> str:
    """Exchange a classic PAT for a read-only token scoped to one GHCR package."""
    if repo not in REPOSITORIES or username != "hensonzh" or not pat:
        raise ValueError("unapproved GHCR package or credential")
    scope = f"repository:hensonzh/{repo}:pull"
    query = urlencode({"service": "ghcr.io", "scope": scope})
    basic = base64.b64encode(f"{username}:{pat}".encode()).decode()
    request = urllib.request.Request(
        f"https://ghcr.io/token?{query}", headers={"Authorization": "Basic " + basic},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    bearer = data.get("token") if isinstance(data, dict) else None
    if not isinstance(bearer, str) or not bearer:
        raise ValueError("GHCR did not grant a scoped registry token")
    return bearer


def fetch_layout(args: argparse.Namespace) -> None:
    validate(args.repo, args.digest, args.output)
    token_file = args.token_file or args.registry_token_file
    private_root = PRIVATE_ROOT
    if (token_file is None or token_file.parent != private_root or private_root.is_symlink()
            or not private_root.is_dir() or stat.S_IMODE(private_root.stat().st_mode) != 0o700
            or private_root.stat().st_uid != os.geteuid() or token_file.is_symlink()
            or not token_file.is_file() or stat.S_IMODE(token_file.stat().st_mode) != 0o600
            or token_file.stat().st_uid != os.geteuid() or token_file.stat().st_nlink != 1):
        raise ValueError("B GHCR token must be a private tmpfs file")
    token = token_file.read_text().strip()
    if not token:
        raise ValueError("B GHCR token is empty")
    bearer = (registry_token(args.repo, token, username="hensonzh")
              if args.token_file else token)
    base = f"https://ghcr.io/v2/hensonzh/{args.repo}"

    def fetch(kind: str, digest: str) -> bytes:
        if not DIGEST.fullmatch(digest):
            raise ValueError("registry descriptor is not a SHA256 digest")
        req = urllib.request.Request(
            f"{base}/{kind}/{digest}",
            headers={"Authorization": "Bearer " + bearer, "Accept": ACCEPT},
        )
        with urllib.request.urlopen(req, timeout=90) as response:
            content = response.read()
        if "sha256:" + hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("registry content digest mismatch")
        return content

    with tempfile.TemporaryDirectory(prefix="momcozy-b-oci-") as temporary:
        folder = Path(temporary)
        blobs = folder / "blobs/sha256"
        blobs.mkdir(parents=True)
        (folder / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n')

        def put(content: bytes) -> None:
            (blobs / hashlib.sha256(content).hexdigest()).write_bytes(content)

        index = json.loads(fetch("manifests", args.digest))
        children = [
            descriptor for descriptor in index.get("manifests", [])
            if descriptor.get("platform") == {"os": "linux", "architecture": "amd64"}
        ]
        if len(children) != 1:
            raise ValueError("expected exactly one linux/amd64 child")
        child = children[0]
        manifest_raw = fetch("manifests", child["digest"])
        if len(manifest_raw) != child["size"]:
            raise ValueError("child manifest size mismatch")
        manifest = json.loads(manifest_raw)
        for descriptor in (manifest["config"], *manifest["layers"]):
            content = fetch("blobs", descriptor["digest"])
            if len(content) != descriptor["size"]:
                raise ValueError("image blob size mismatch")
            put(content)
        put(manifest_raw)
        (folder / "index.json").write_text(json.dumps({"schemaVersion": 2, "manifests": [child]}))
        # Only digest-named known files enter the archive; no arbitrary paths.
        # Open outside try: an existing archive must never be removed on failure.
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream, tarfile.open(fileobj=stream, mode="w") as archive:
                for filename in ("oci-layout", "index.json"):
                    archive.add(folder / filename, arcname=filename, recursive=False)
                for file in blobs.iterdir():
                    archive.add(file, arcname="blobs/sha256/" + file.name, recursive=False)
        except Exception:
            args.output.unlink(missing_ok=True)
            raise
    print(f"B {args.repo} verified OCI index {args.digest}; archive size {args.output.stat().st_size}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", choices=REPOSITORIES, required=True)
    parser.add_argument("--digest", required=True)
    credentials = parser.add_mutually_exclusive_group(required=True)
    credentials.add_argument("--token-file", type=Path, help="Classic PAT in private tmpfs; exchange for scoped registry token")
    credentials.add_argument("--registry-token-file", type=Path, help="Preissued, short-lived read-only registry token in private tmpfs")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        fetch_layout(args)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        print("FAIL B OCI digest delivery; no image accepted", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
